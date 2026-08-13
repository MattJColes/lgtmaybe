"""LiteLLMProvider: the litellm adapter implementing ProviderClient.

Wraps litellm.completion with retry (tenacity), an explicit timeout, and an
optional fallback model.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import litellm

# litellm re-exports the openai exception types but doesn't list them in __all__,
# so mypy rejects litellm.AuthenticationError etc. as un-exported — import them
# from litellm.exceptions, where they're defined directly.
from litellm.exceptions import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from litellm.utils import supports_prompt_caching
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_before_delay,
    wait_exponential_jitter,
)

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    ProviderResult,
    attempts_of,
    stamp_attempts,
    stamp_unrecoverable,
)
from lgtmaybe.core.ports import (
    Message,
    ProviderClient,
    ProviderTruncated,
    ProviderWallTimeout,
)
from lgtmaybe.providers.constants import CLOUD_TIMEOUT

_log = get_logger(__name__)

_MAX_ATTEMPTS = 4

# All attempts for one completion share a total wall-clock budget of this many
# times the per-request timeout, so a flaky model can't burn
# attempts × timeout + backoff per call. 2.5× leaves room for one full-length
# failure, a retry, and the backoff between them; a call that needs more than
# that is better failed and surfaced than ground on.
_CALL_BUDGET_MULTIPLIER = 2.5

# Prompt caching (of the shared prefix) is applied on routes where an EXPLICIT
# cache breakpoint is the mechanism:
#
# - ``anthropic/`` — cache_control, natively;
# - ``bedrock/`` — litellm translates cache_control to a Converse cachePoint
#   for Claude and Nova;
# - ``vertex_ai/`` — both families, per litellm's documented support: the
#   Anthropic partner-model route reads cache_control off the messages and sets
#   the prompt-caching beta from it, and for Gemini litellm translates the same
#   marker into Google's cachedContents API;
# - ``zai/`` — ZAIChatConfig overrides litellm's strip step to always preserve
#   cache_control, so GLM / Zhipu gets the breakpoint;
# - ``openrouter/`` — marked for the WHOLE route on purpose. litellm's
#   OpenrouterConfig keeps cache_control for the families that accept it
#   (claude, gemini, minimax, glm, z-ai) and removes it for every other model
#   before the request goes out, so a per-model allowlist here would only
#   duplicate — and drift from — that list. Marking a deepseek or qwen model is
#   a no-op upstream, not an error.
#
# Other providers either cache automatically server-side with no marker (OpenAI,
# Azure, DeepSeek direct) or don't cache at all — for them the request is sent
# unchanged. They still benefit from the split prefix shape, which keeps the
# cached region byte-identical across the lens fan-out.
_CACHE_CONTROL_PREFIXES = (
    "anthropic/",
    "bedrock/",
    "vertex_ai/",
    "zai/",
    "openrouter/",
)

# The LOWEST documented minimum cacheable block across the marked routes, not
# the highest. A prefix under a model's own minimum is silently not cached (no
# error), and the minimum is per-model: 1,024 for Claude Sonnet 4.x/Opus 4-4.1
# and Gemini 2.5 Flash; 2,048 for Claude Haiku 3.5; 4,096 for Claude Opus
# 4.5+/Haiku 4.5 and Gemini 2.5 Pro. Gating on the lowest means we mark
# whenever caching *could* engage — raising this to the highest would stop
# marking in the 1k-4k range and lose real caching on the 1,024-token models.
# Below the floor, though, no model can cache, so the marker is pure
# request-shape churn: skip it and send the message unchanged.
_MIN_CACHEABLE_TOKENS = 1024

# Errors that retrying cannot fix: bad credentials, a malformed/unsupported
# request, an unknown model, a denied permission, and a content-policy block
# (ContentPolicyViolationError subclasses BadRequestError, so it's covered).
# Retrying these just burns the backoff budget — and, stacked across every lens,
# turns an instant failure into many minutes of wasted runner time (the gpt-5.5
# quota failure that ran ~13 min before surfacing). Fail fast instead.
_PERMANENT_ERROR_TYPES: tuple[type[Exception], ...] = (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
)

# A 429 is overloaded: a *capacity* rate-limit is transient (back off and retry),
# but a *quota/billing* rate-limit is permanent (out of credit — retrying never
# recovers). Both arrive as RateLimitError, so we tell them apart by message.
_QUOTA_MARKERS = (
    "insufficient_quota",
    "exceeded your current quota",
    "check your plan and billing",
)

# Running out of prepaid credit is permanent, and it does NOT arrive as a
# RateLimitError: OpenRouter (and other prepaid-balance routes) reject the request
# up front with a generic APIError, so the type check above and the 429 check below
# both miss it. The refusal is a *pre-flight reservation* failure — the route costs
# prompt + max_tokens against the balance before generating a single token, and an
# uncapped request reserves the model's full output ceiling — so the balance can't
# grow mid-review and every retry is guaranteed to fail identically.
_INSUFFICIENT_CREDIT_MARKERS = (
    "requires more credits",
    "can only afford",
    "insufficient credits",
    "insufficient_credits",
)

# An expired/invalid cloud credential is permanent — a retry can't refresh it, so
# storming the backoff over every lens just delays an inevitable failure (the same
# wasted-runner-time trap as a quota error). Unlike a bad *static* key (which
# litellm raises as AuthenticationError, already caught above), an expired ambient
# token (Bedrock STS, Vertex ADC) comes back as a 403 that litellm maps to a
# generic APIConnectionError — indistinguishable by type from a transient ollama
# "connection refused" that must still be retried. So we tell them apart by
# message, exactly as with quota vs. capacity above.
_EXPIRED_CREDENTIAL_MARKERS = (
    "security token included in the request is expired",
    "security token included in the request is invalid",
    "expiredtoken",
    "expired credential",
    "the credential provided is expired",
)


def _mentions(exc: BaseException, markers: tuple[str, ...]) -> bool:
    """True when *exc*'s message mentions any of *markers* (case-insensitively)."""
    msg = str(exc).lower()
    return any(marker in msg for marker in markers)


def _is_unrecoverable(exc: BaseException) -> bool:
    """True when NO later attempt at this call can succeed — not in this run.

    Bad credentials, exhausted quota, spent prepaid credit, a request the model
    refuses: the condition cannot change while the review runs. Narrower than
    :func:`_is_permanent`, which additionally refuses to retry a call *in place*
    when only an identical immediate re-send is on offer. The engine reads this
    one (via the stamp in :func:`stamp_unrecoverable`) to decide whether its
    rescue wave is worth a billed call — and a stalled upstream, which is
    permanent for an immediate retry, may well answer a genuinely later request.
    """
    if isinstance(exc, _PERMANENT_ERROR_TYPES):
        return True
    # A 429 is permanent only when it's a quota/billing one; a capacity 429 retries.
    if isinstance(exc, RateLimitError):
        return _mentions(exc, _QUOTA_MARKERS)
    return _mentions(exc, _EXPIRED_CREDENTIAL_MARKERS) or _mentions(
        exc, _INSUFFICIENT_CREDIT_MARKERS
    )


def _is_permanent(exc: BaseException) -> bool:
    """True when retrying *exc* cannot plausibly succeed, so we should not."""
    if _is_unrecoverable(exc):
        return True
    # A blown wall clock is not a blip: the retry re-sends the identical request
    # against the identical budget, so attempts 2..N can only fail the same way
    # and cost another full timeout each. On the generous slow-provider default
    # that turned one stuck lens into an hour of runner time before the failure
    # surfaced. One attempt, then say so — and let the fallback model (a genuinely
    # different request) still have its turn.
    if isinstance(exc, ProviderWallTimeout):
        return True
    # Nor is a blown output ceiling: at temperature 0 the identical request runs
    # to the identical ceiling, and each attempt costs a full ceiling-length
    # generation — 65,536 output tokens and 21 minutes, in the run that prompted
    # this. Same bargain as above: one attempt, then say so, fallback still tried.
    # This stays permanent even though the ENGINE now retries a truncated call by
    # splitting the batch: the two are not in tension, they are the division of
    # labour. Only the engine holds the batch, so only the engine can change the
    # payload; the adapter can only re-send the same oversized one, which is the
    # attempt worth refusing.
    return isinstance(exc, ProviderTruncated)


# How long to back off between attempts, by what failed.
#
# The generic ladder is for a blip — a connection reset, an ollama server still
# warming up, a 5xx. Sub-second is right there: the condition is gone by the time
# the next request lands.
#
# A capacity 429 is not that. Gateways that meter a key — OpenRouter above all —
# meter it per MINUTE, so the generic ladder (with tenacity's default `jitter=1`
# that is roughly 1.1s + 1.2s + 1.4s) puts all four attempts inside the SAME
# window, where they can only fail identically. That is how three consecutive
# reviews each came back "1 of 4 review calls failed" on a rate limit while the
# other three lenses sailed through. So a rate limit gets its own, much slower
# ladder: long enough for the next attempt to land in a fresh window.
#
# Both stay well inside the call's existing `stop_after_delay` budget
# (2.5 × timeout — 1,500s on direct cloud, 4,500s on the slow-capable routes
# openrouter/ollama/openai-compatible), so nothing new needs bounding.
_RATE_LIMIT_BACKOFF_INITIAL = 5.0
_RATE_LIMIT_BACKOFF_MAX = 60.0

# The most we will honour from a server-supplied `Retry-After`. A gateway that
# asks for an hour is reporting a limit no single review can outwait, and
# sleeping on it would burn the whole run's wall clock inside one lens. Clamp,
# let the attempt fail, and let the engine's rescue wave (or, failing that, the
# incomplete-results notice) be what handles it.
_RETRY_AFTER_CEILING = 120.0

_generic_wait = wait_exponential_jitter(initial=0.1, max=5)
_rate_limit_wait = wait_exponential_jitter(
    initial=_RATE_LIMIT_BACKOFF_INITIAL, max=_RATE_LIMIT_BACKOFF_MAX
)


def _response_headers(exc: BaseException) -> Mapping[str, str] | None:
    """The response headers *exc* carries, from wherever litellm put them.

    Three places, because litellm's own ``_get_response_headers`` looks in the
    same three: the exception's ``headers`` attribute, the ``response`` it wraps,
    and the ``litellm_response_headers`` its exception mapper stamps on the way
    out. Which one is populated depends on the route, so reading only one loses
    the header on the others.
    """
    for source in (
        getattr(exc, "headers", None),
        getattr(getattr(exc, "response", None), "headers", None),
        getattr(exc, "litellm_response_headers", None),
    ):
        if source:
            return source  # type: ignore[no-any-return]
    return None


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Seconds *exc*'s ``Retry-After`` asks us to wait, or None if it said nothing.

    RFC 9110 allows either delta-seconds or an HTTP-date, and the Cloudflare edge
    these gateways sit behind sends both forms, so both are read. A stale date
    floors at zero rather than going negative, and an unparseable value is
    treated as no header at all — a malformed hint must never crash the retry
    loop, which is the one place left that can still rescue the call.

    Deliberately only ``Retry-After``: it is the one header every 429 route
    sends, and it says what we actually need in the units we need it. The
    ``X-RateLimit-Reset`` families disagree on units (seconds here, epoch
    milliseconds there) between providers, so parsing them would be guesswork
    dressed as precision.
    """
    headers = _response_headers(exc)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = None
    if seconds is not None:
        # `float()` parses "nan" and "inf" perfectly happily, and either would
        # travel through the clamp below into tenacity's sleep — raising from
        # inside the retry loop, the one place left that can still rescue this
        # call. A header we can't use is a header we don't have.
        return max(0.0, seconds) if math.isfinite(seconds) else None
    try:
        when = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _retry_wait(retry_state: RetryCallState) -> float:
    """How long to wait before the next attempt, chosen by what just failed."""
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome is not None else None
    if isinstance(exc, RateLimitError):
        after = _retry_after_seconds(exc)
        if after is not None:
            # The server told us when it will take us back. Nothing we compute
            # can beat that, so honour it — up to the ceiling above.
            return min(after, _RETRY_AFTER_CEILING)
        return _rate_limit_wait(retry_state)
    return _generic_wait(retry_state)


def _rejects_response_format(exc: Exception) -> bool:
    """True when an API error means the model won't take our structured-output param.

    Bedrock's Converse endpoint is the case that matters. For a model whose route
    doesn't implement structured outputs, litellm still translates
    ``response_format`` into a Converse ``output_config.format`` field, and the
    service rejects the whole request: ``BedrockException - {"message":"The model
    returned the following errors: output_config.format: Extra inputs are not
    permitted"}``. That arrives as a 400 — permanent, so it is never retried —
    and since every lens sends the same shape, one unsupported field failed the
    entire review with "every review call failed".

    ``drop_params`` cannot help here: it drops params litellm's capability map
    says a model lacks, and for these models the map says the param is supported.
    So the rejection is read off the error instead: the field name plus a
    rejection phrase, matched loosely because the wording is the backend's, not
    litellm's. A false positive costs one re-send without the param (the prompt
    still asks for JSON and the parser is lenient); a false negative costs the
    whole review.
    """
    msg = str(exc).lower()
    if not any(field in msg for field in ("response_format", "output_config", "responseformat")):
        return False
    return any(
        phrase in msg
        for phrase in ("not permitted", "not supported", "unsupported", "unknown", "unexpected")
    )


def _rejects_temperature(exc: Exception) -> bool:
    """True when an API error means the model accepts the ``temperature`` param
    but not the value we sent (e.g. OpenAI's gpt-5.x: "'temperature' does not
    support 0 ... Only the default (1) value is supported"). ``drop_params`` does
    not catch this — the param is supported, only the value is rejected."""
    msg = str(exc).lower()
    return "temperature" in msg and (
        "does not support" in msg or "unsupported value" in msg or "only the default" in msg
    )


# We always send ``temperature`` (for determinism) and ``response_format`` (for
# structured JSON output), but not every model accepts them: some bedrock-hosted
# models (e.g. ``openai.gpt-5.5``) reject both and litellm raises
# ``UnsupportedParamsError``, which would fail the entire review. Enabling
# drop_params makes litellm consult its per-model capability map and silently
# drop only the params a given model can't take — keeping them for the local
# (ollama) and cloud models that do support them. The prompt also asks for JSON,
# and the parser is lenient, so a dropped ``response_format`` still parses.
litellm.drop_params = True

# litellm prints a "Give Feedback / Get Help" + "LiteLLM.Info:" banner (and a
# "Provider List:" one) straight to STDOUT whenever it maps a provider error.
# stdout is our machine-readable channel: on `lgtmaybe review --json` the banner
# lands in front of the findings array and breaks json.load on the output — and
# it contains a "[", so even a "find the first bracket" recovery picks the wrong
# one. Nothing is lost by silencing it: the error itself still surfaces through
# the exception and our own structured logging, which goes to stderr.
litellm.suppress_debug_info = True


class LiteLLMProvider(ProviderClient):
    """ProviderClient backed by litellm with retry and optional fallback."""

    def __init__(
        self,
        *,
        model: str = "",
        fallback_model: str | None = None,
        prompt_cache: bool = False,
        **default_opts: Any,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model
        # Mark the static system prompt cacheable (cache_control) on routes that
        # support an explicit cache breakpoint; a safe no-op everywhere else.
        # A named constructor arg — NOT part of default_opts — so it can never
        # leak into the litellm.completion call as an unknown parameter.
        self.prompt_cache = prompt_cache
        self.default_opts: dict[str, Any] = default_opts
        # Set once a structured-output call comes back empty (see _call): the
        # backend's JSON-schema decoder is broken for this model, so every
        # subsequent call skips response_format up front instead of paying a
        # wasted empty round-trip first. Sticky for the life of this provider.
        self._skip_response_format = False
        # Memoized supports-cache-control answers: the review fans out many
        # completions on the same model string, and the capability lookup is a
        # pure function of it. Instance-scoped (not lru_cache) so tests that
        # patch the litellm lookup stay isolated.
        self._cache_capable: dict[str, bool] = {}

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        merged = {**self.default_opts, **opts}
        merged.setdefault("timeout", CLOUD_TIMEOUT)
        # We own retries (tenacity, below). Disable litellm's own retry loop so a
        # failure isn't ground through two stacked backoff layers — the doubling
        # that helped a quota error run ~13 min before it surfaced.
        merged.setdefault("num_retries", 0)
        # Sticky-routing / cache-routing hint, keyed on the shared prefix.
        # OpenRouter pins a conversation to one provider endpoint to keep its
        # cache warm, but with no key it only starts doing so AFTER it observes
        # a cache hit — too late for a fan-out that dispatches its lenses at
        # once. OpenAI uses the same field as a hint for prefix-sharing
        # requests, so one param serves both; drop_params strips it elsewhere.
        if self.prompt_cache:
            merged.setdefault("prompt_cache_key", _prefix_cache_key(messages))
        # A factory-built provider carries the resolved litellm model string
        # (e.g. "ollama/qwen3:27b"); prefer it over the caller's raw cfg.model.
        effective_model = self.model or model
        try:
            return self._complete_with_retry(messages, effective_model, **merged)
        except Exception as exc:
            if self.fallback_model is None:
                raise
            # The primary's requests were still issued and still billed, so they
            # stay in the total: a fallback rescue that reported one request would
            # hide the fact that the primary burned its budget first.
            spent = attempts_of(exc)
            try:
                result = self._complete_with_retry(messages, self.fallback_model, **merged)
            except BaseException as fallback_exc:
                stamp_attempts(fallback_exc, spent + attempts_of(fallback_exc))
                raise
            return result.model_copy(update={"attempts": result.attempts + spent})

    def _complete_with_retry(
        self, messages: list[Message], model: str, **kwargs: Any
    ) -> ProviderResult:
        # Applied per effective model (the primary and the fallback can differ
        # in cache support), and to a copy — the caller's messages are not ours
        # to mutate.
        messages = self._with_cache_control(messages, model)
        # Counted here (not read from tenacity's statistics) so the number lands
        # on the result even when the mocked/fake retry layer changes shape — and
        # counted per REQUEST, not per tenacity attempt: one attempt can issue a
        # second request (the empty-structured-output and rejected-temperature
        # re-sends below), and a count that missed those would understate what the
        # call actually cost, which is the whole point of reporting it.
        attempts = 0

        def count_request() -> None:
            nonlocal attempts
            attempts += 1

        # Attempts share one deadline derived from the per-request timeout (the
        # fallback model, when configured, gets its own fresh budget — it is a
        # separate recovery path, not another attempt).
        timeout = float(kwargs.get("timeout") or CLOUD_TIMEOUT)

        @retry(
            retry=retry_if_exception(lambda exc: not _is_permanent(exc)),
            # stop_BEFORE_delay, not after: `after` reads the clock only once a
            # wait has already been taken, so a call with most of its budget
            # spent could still sleep out a full `Retry-After` on top of it —
            # two minutes past a budget that had all but run out. `before` weighs
            # the UPCOMING sleep, so a wait that would blow the budget ends the
            # call instead of being taken.
            stop=stop_after_attempt(_MAX_ATTEMPTS)
            | stop_before_delay(timeout * _CALL_BUDGET_MULTIPLIER),
            wait=_retry_wait,
            reraise=True,
        )
        def _call() -> ProviderResult:
            # A prior call already proved this model's JSON-schema mode returns
            # empty, so don't pay the wasted round-trip again — drop it up front.
            if self._skip_response_format:
                kwargs.pop("response_format", None)
            result = self._raw_completion(model, messages, kwargs, count_request)
            # Some grammar-constrained backends (notably LM Studio fronting a
            # "thinking" model like qwen3.x) return EMPTY content under a
            # response_format JSON schema — the schema decoder yields nothing. The
            # prompt already asks for JSON and the parser is lenient, so drop the
            # schema and retry once: the model then emits the findings as normal
            # (fenced) text we can parse. Remember it so later calls skip it too.
            if not result.text.strip() and kwargs.get("response_format") is not None:
                self._skip_response_format = True
                kwargs.pop("response_format")
                result = self._raw_completion(model, messages, kwargs, count_request)
            return result

        try:
            result = _call()
        except BaseException as exc:
            # A failure has no ProviderResult to ride home on, so the count goes
            # on the exception — else the instrumentation records a budget-burning
            # failure as `attempts=0`, which reads as "never retried". The same
            # channel carries whether any later attempt could have helped, which
            # is what stops the engine's rescue wave re-billing a dead key.
            stamp_attempts(exc, attempts)
            if _is_unrecoverable(exc):
                stamp_unrecoverable(exc)
            raise
        return result.model_copy(update={"attempts": attempts})

    def _raw_completion(
        self,
        model: str,
        messages: list[Message],
        kwargs: dict[str, Any],
        count_request: Callable[[], None] = lambda: None,
    ) -> ProviderResult:
        """One litellm call, re-sent without any param the model just rejected.

        ``count_request`` is called immediately before each request actually goes
        out, so the reported attempt count matches the number of model calls this
        made — including the re-sends below, each a second billed request.
        """
        while True:
            count_request()
            try:
                return self._map_response(
                    _completion_with_wall_timeout(model, messages, kwargs), model
                )
            except Exception as exc:
                if not self._drop_rejected_param(exc, kwargs):
                    raise

    def _drop_rejected_param(self, exc: Exception, kwargs: dict[str, Any]) -> bool:
        """Strip the one request param *exc* says this model won't take.

        Two params are sent for review quality rather than necessity —
        ``temperature`` (determinism) and ``response_format`` (structured output)
        — and a model that refuses either would otherwise fail the whole review
        over a preference. Both refusals are permanent 400s, so the recovery is
        to drop the param and re-send; ``kwargs`` is the dict every later retry
        of this call reuses, so the drop sticks for them too.

        Returns True when something was dropped and the call is worth re-sending.
        """
        if "temperature" in kwargs and _rejects_temperature(exc):
            # The param is accepted, only our value isn't — let the model use its
            # own default.
            kwargs.pop("temperature")
            return True
        if kwargs.get("response_format") is not None and _rejects_response_format(exc):
            # Remembered for the whole provider, not just this call: the lens
            # fan-out sends the same shape N times, and without this every one of
            # them pays its own rejected round-trip first.
            self._skip_response_format = True
            kwargs.pop("response_format")
            return True
        return False

    def _with_cache_control(self, messages: list[Message], model: str) -> list[Message]:
        """Return *messages* shaped for the model's caching route, when it pays.

        The engine sends the review prompt in a **split shape** when
        ``prompt_cache`` is on: a lens-independent system preamble, then the
        shared prefix (the wrapped diff, plus hints) as one user message, then
        the lens-specific instruction as a final user message. This adapter is
        where that shape meets each provider:

        - On a route with an explicit cache breakpoint (:data:`_CACHE_CONTROL_PREFIXES`,
          confirmed by litellm's capability map): consecutive user messages are
          merged into ONE user message of text blocks, with ``cache_control``
          on the system prompt and on the last *prefix* block — so every call
          in the fan-out after the first reads the whole preamble-plus-diff
          prefix from cache. The final (lens) block stays uncached. A
          breakpoint is only emitted once the prefix it closes clears the
          1,024-token minimum cacheable block (smaller is silently not cached,
          so the marker would be pure request-shape churn). litellm exposes no
          per-model threshold (some Opus/Haiku-class models need 4,096), so
          1,024 is used for all — a too-small block degrades to "not cached",
          never to an error.
        - Everywhere else (no breakpoint route, capability lookup failure, or
          ``prompt_cache`` off): consecutive user messages are merged into one
          plain string and no marker is attached — byte-identical to the single
          user message these providers have always received.
        """
        if not self.prompt_cache:
            return _merge_user_messages(messages)
        if model not in self._cache_capable:
            self._cache_capable[model] = _supports_cache_control(model)
        if not self._cache_capable[model]:
            return _merge_user_messages(messages)

        from lgtmaybe.engine.compress import count_tokens

        out = list(messages)
        cumulative = 0
        sys_index = next((i for i, m in enumerate(out) if m.get("role") == "system"), None)
        if sys_index is not None and isinstance(out[sys_index].get("content"), str):
            sys_text = out[sys_index]["content"]
            cumulative = count_tokens(sys_text)
            if cumulative >= _MIN_CACHEABLE_TOKENS:
                sys_marked: Any = [_cache_block(sys_text)]
                out[sys_index] = {**out[sys_index], "content": sys_marked}

        run_start, run = _trailing_user_run(out)
        if len(run) >= 2:
            # Split shape: every user message but the last is shared prefix.
            prefix_texts = [str(m.get("content", "")) for m in run[:-1]]
            cumulative += sum(count_tokens(t) for t in prefix_texts)
            blocks: list[dict[str, Any]] = [{"type": "text", "text": t} for t in prefix_texts]
            if cumulative >= _MIN_CACHEABLE_TOKENS:
                blocks[-1] = _cache_block(prefix_texts[-1])
            blocks.append({"type": "text", "text": str(run[-1].get("content", ""))})
            merged: Any = blocks
            out[run_start:] = [{"role": "user", "content": merged}]
        return out

    def _map_response(self, response: Any, model: str) -> ProviderResult:
        # Some providers return null content (e.g. a model that answered only via
        # a reasoning channel under JSON mode); treat that as empty, not a crash.
        text: str = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens: int = usage.prompt_tokens
        output_tokens: int = usage.completion_tokens
        # A generation that stopped because it ran out of output tokens is cut off
        # mid-token: it can never parse, and passing it on gets it reported as
        # "unparseable model output", which points at the prompt rather than at
        # the ceiling actually hit. Raised here so the failure names itself. The
        # body travels with it: unusable as an answer, but the findings finished
        # before the cut are real, and the engine salvages them from it.
        #
        # The ceiling is named as `max_tokens`, NOT as "the model's limit": in the
        # run that prompted this it was lgtmaybe's own configured 16,384 against a
        # model good for 65,536, so blaming the model sends the reader to a knob
        # they cannot move. The reasoning count is named when the route reports it,
        # because it is the whole explanation for a fifteen-line diff truncating —
        # a reasoning model spends this same budget on thought before it writes a
        # single finding, so the cap, not the diff, is what needs raising.
        if _finish_reason(response) == "length":
            reasoning = _reasoning_tokens(usage)
            detail = f" ({reasoning} reasoning)" if reasoning else ""
            raise ProviderTruncated(
                f"response hit the {output_tokens}-token `max_tokens` ceiling{detail} before "
                "finishing — raise `max_tokens`, or lower `max_input_tokens` so each call "
                "covers less",
                text=text,
                # The same two numbers as the message, carried as data: the engine
                # decides whether shrinking the payload can help from the ratio
                # between them, and re-reading them out of the prose above would be
                # parsing our own sentence. None, not 0, when the route reported no
                # breakdown — "it never said" must not read as "it thought nothing".
                reasoning_tokens=reasoning or None,
                output_tokens=output_tokens,
            )
        cache_read, cache_creation = _cache_usage(usage)
        if cache_read or cache_creation:
            # Instrumentation: whether the static prefix hit or (re)wrote the
            # cache — a run whose reads never climb has a busted prefix.
            _log.info(
                "prompt cache usage",
                extra={
                    "model": model,
                    "cache_read_tokens": cache_read,
                    "cache_creation_tokens": cache_creation,
                },
            )

        return ProviderResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            # Same helper as the truncation message above, so a call that
            # succeeded and a call that hit the ceiling can never report the
            # thinking they did by two different readings of the same field.
            reasoning_tokens=_reasoning_tokens(usage),
        )


def _finish_reason(response: Any) -> str | None:
    """The response's finish reason, or None when the route doesn't report one.

    Only ``length`` is acted on, and only when the provider says it plainly.
    litellm normalises the field through a fixed map and quietly rewrites
    anything it doesn't recognise to ``stop`` (it logs "Unmapped finish_reason
    '<x>', defaulting to 'stop'"), so a route that reports a ceiling hit under
    its own name — OpenRouter answers ``error`` — arrives here indistinguishable
    from a clean finish. That case is caught downstream instead, by the parser
    noticing the JSON never closed.
    """
    choice = response.choices[0]
    reason = getattr(choice, "finish_reason", None)
    return str(reason) if reason is not None else None


def _prefix_cache_key(messages: list[Message]) -> str:
    """A stable routing key identifying this call's *cacheable prefix*.

    Everything except the final message, which is the per-lens block that sits
    outside the cached region. So every lens of a batch produces the same key
    (they share a prefix and should share a cache), while another PR — or the
    reflection pass, which has its own preamble — produces a different one.

    A digest, not the content: it travels to the provider as a routing hint,
    and it is bounded well inside OpenRouter's 256-character ceiling.
    """
    prefix = "".join(f"{m.get('role')}:{m.get('content')!r}\n" for m in messages[:-1])
    return "lgtmaybe-" + hashlib.sha256(prefix.encode("utf-8", "replace")).hexdigest()[:32]


def _cache_block(text: str) -> dict[str, Any]:
    """A text content block carrying an ephemeral cache breakpoint."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _completion_with_wall_timeout(
    model: str, messages: list[Message], kwargs: dict[str, Any]
) -> Any:
    """Call LiteLLM with a real wall-clock bound even if its transport hangs.

    The deadline is owned by the monotonic clock, not delegated to a single
    ``Future.result(timeout=...)`` wait: on a coarse-timer platform (Windows'
    default granularity is ~15.6ms) that wait can resolve either side of the
    deadline, which made "did this time out?" a question about thread scheduling
    rather than about elapsed time. Re-checking the clock keeps the bound honest and
    puts the measured wait in the error.

    A call that *did* complete, even a hair past the deadline, is still honoured —
    discarding it would waste a response the provider already billed for and, worse,
    replace its real error (a quota 429, a bad key) with a bare timeout, hiding the
    one thing that tells the user what to fix.
    """
    timeout = float(kwargs.get("timeout") or CLOUD_TIMEOUT)
    future: Future[Any] = Future()

    def complete() -> None:
        try:
            future.set_result(litellm.completion(model=model, messages=messages, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)

    start = time.monotonic()
    deadline = start + timeout
    threading.Thread(target=complete, name="lgtmaybe-provider", daemon=True).start()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return future.result(timeout=remaining)
        except FutureTimeoutError:
            continue  # the wait resolved early — trust the clock, not the wakeup
    if future.done():
        return future.result()
    raise ProviderWallTimeout(
        f"provider request exceeded {timeout:g}s (waited {time.monotonic() - start:.3f}s)"
    )


def _trailing_user_run(messages: list[Message]) -> tuple[int, list[Message]]:
    """The trailing run of consecutive user messages: ``(start_index, run)``."""
    end = len(messages)
    start = end
    while start > 0 and messages[start - 1].get("role") == "user":
        start -= 1
    return start, messages[start:end]


def _merge_user_messages(messages: list[Message]) -> list[Message]:
    """Collapse a trailing run of user messages into one plain-string message.

    The engine's split prompt shape carries the shared prefix and the lens
    instruction as separate user messages; providers that take no cache marker
    get them joined back into the single user message they have always
    received. Anything else (one user message, non-string content) passes
    through untouched.
    """
    start, run = _trailing_user_run(messages)
    if len(run) < 2 or not all(isinstance(m.get("content"), str) for m in run):
        return messages
    joined = "\n\n".join(str(m.get("content")) for m in run)
    return [*messages[:start], {"role": "user", "content": joined}]


def _supports_cache_control(model: str) -> bool:
    """Whether *model*'s route takes an explicit cache_control breakpoint.

    Feature detection, not configuration: the route must be one where litellm
    forwards ``cache_control`` (see :data:`_CACHE_CONTROL_PREFIXES`), and
    litellm's model-capability map must say the model itself caches. Any lookup
    failure (an unknown or freshly released model) means "don't mark" — caching
    is an optimisation, never worth an error.

    That capability map has gaps (it answers False for, say, a dated
    ``vertex_ai/claude-3-5-sonnet@…`` id that does cache). A gap costs a missed
    discount, never a broken call, so the conservative answer stays the right
    one — and the split prefix shape still helps a model we decline to mark.
    """
    if not model.startswith(_CACHE_CONTROL_PREFIXES):
        return False
    try:
        return bool(supports_prompt_caching(model=model))
    except Exception:
        return False


def _reasoning_tokens(usage: Any) -> int:
    """Output tokens the model spent thinking, or 0 when the route doesn't say.

    litellm normalises this onto ``completion_tokens_details.reasoning_tokens``
    for the routes that report it. Read defensively — a route that omits the
    wrapper, or fills it with a non-number, must not turn a truncation report
    into a crash — and 0 simply means "no breakdown to show".
    """
    details = getattr(usage, "completion_tokens_details", None)
    value = getattr(details, "reasoning_tokens", None)
    return value if isinstance(value, int) and value > 0 else 0


def _cache_usage(usage: Any) -> tuple[int, int]:
    """Extract (cache_read, cache_creation) token counts from a usage object.

    Anthropic/Bedrock report ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens`` at the top level. Everything else arrives
    through litellm's normalised ``prompt_tokens_details`` wrapper —
    OpenAI-style ``cached_tokens`` (which is also where DeepSeek's
    ``prompt_cache_hit_tokens`` and an OpenRouter response land), plus a write
    count under one of two names: litellm's own ``cache_creation_tokens`` or
    OpenRouter's ``cache_write_tokens``, which litellm passes straight through
    (its wrapper accepts extra fields, and it does not translate the name).

    Every name is checked, because a count that exists but isn't read back
    reports 0 — and in ``--profile`` a zero is indistinguishable from caching
    never having engaged at all. All optional; genuinely absent means 0.
    """
    details = getattr(usage, "prompt_tokens_details", None)

    def _detail(*names: str) -> Any:
        for name in names:
            value = getattr(details, name, None) if details is not None else None
            if value:
                return value
        return None

    cache_read = getattr(usage, "cache_read_input_tokens", None) or _detail("cached_tokens")
    cache_creation = getattr(usage, "cache_creation_input_tokens", None) or _detail(
        "cache_creation_tokens", "cache_write_tokens"
    )
    return int(cache_read or 0), int(cache_creation or 0)
