"""LiteLLMProvider: the litellm adapter implementing ProviderClient.

Wraps litellm.completion with retry (tenacity), an explicit timeout, and an
optional fallback model.
"""

from __future__ import annotations

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
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential_jitter,
)

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import ProviderResult
from lgtmaybe.core.ports import Message, ProviderClient

_log = get_logger(__name__)

_DEFAULT_TIMEOUT = 60  # seconds
_MAX_ATTEMPTS = 4

# All attempts for one completion share a total wall-clock budget of this many
# times the per-request timeout, so a flaky model can't burn
# attempts × timeout + backoff per call (four 60s timeouts plus waits is over
# four minutes — per lens). 2.5× leaves room for one full-length failure, a
# retry, and the backoff between them; a call that needs more than that is
# better failed and surfaced than ground on.
_CALL_BUDGET_MULTIPLIER = 2.5

# Prompt caching (of the static system prompt) is applied only on routes where
# an EXPLICIT cache breakpoint is the mechanism: Anthropic direct (cache_control)
# and Bedrock (litellm translates cache_control to a Converse cachePoint for
# Claude and Nova). Other providers either cache automatically server-side with
# no marker (OpenAI, Azure) or don't cache at all (ollama, most
# openai-compatible servers) — for all of them the request is sent unchanged.
_CACHE_CONTROL_PREFIXES = ("anthropic/", "bedrock/")

# Anthropic's documented minimum cacheable block. A smaller prefix is silently
# not cached (some models require even more), so below this the marker is pure
# request-shape churn — skip it and send the message unchanged.
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


def _is_quota_rate_limit(exc: BaseException) -> bool:
    """True for a quota/billing 429 (permanent), False for a capacity 429."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _QUOTA_MARKERS)


def _is_expired_credential(exc: BaseException) -> bool:
    """True when *exc* is an expired/invalid ambient cloud credential (permanent)."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _EXPIRED_CREDENTIAL_MARKERS)


def _is_permanent(exc: BaseException) -> bool:
    """True when retrying *exc* cannot plausibly succeed, so we should not."""
    if isinstance(exc, _PERMANENT_ERROR_TYPES):
        return True
    if isinstance(exc, RateLimitError):
        return _is_quota_rate_limit(exc)
    return _is_expired_credential(exc)


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

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        merged = {**self.default_opts, **opts}
        merged.setdefault("timeout", _DEFAULT_TIMEOUT)
        # We own retries (tenacity, below). Disable litellm's own retry loop so a
        # failure isn't ground through two stacked backoff layers — the doubling
        # that helped a quota error run ~13 min before it surfaced.
        merged.setdefault("num_retries", 0)
        # A factory-built provider carries the resolved litellm model string
        # (e.g. "ollama/qwen3:27b"); prefer it over the caller's raw cfg.model.
        effective_model = self.model or model
        try:
            return self._complete_with_retry(messages, effective_model, **merged)
        except Exception:
            if self.fallback_model is None:
                raise
            return self._complete_with_retry(messages, self.fallback_model, **merged)

    def _complete_with_retry(
        self, messages: list[Message], model: str, **kwargs: Any
    ) -> ProviderResult:
        # Applied per effective model (the primary and the fallback can differ
        # in cache support), and to a copy — the caller's messages are not ours
        # to mutate.
        messages = self._with_cache_control(messages, model)
        # Counted here (not read from tenacity's statistics) so the number lands
        # on the result even when the mocked/fake retry layer changes shape.
        attempts = 0
        # Attempts share one deadline derived from the per-request timeout (the
        # fallback model, when configured, gets its own fresh budget — it is a
        # separate recovery path, not another attempt).
        timeout = float(kwargs.get("timeout") or _DEFAULT_TIMEOUT)

        @retry(
            retry=retry_if_exception(lambda exc: not _is_permanent(exc)),
            stop=stop_after_attempt(_MAX_ATTEMPTS)
            | stop_after_delay(timeout * _CALL_BUDGET_MULTIPLIER),
            wait=wait_exponential_jitter(initial=0.1, max=5),
            reraise=True,
        )
        def _call() -> ProviderResult:
            nonlocal attempts
            attempts += 1
            # A prior call already proved this model's JSON-schema mode returns
            # empty, so don't pay the wasted round-trip again — drop it up front.
            if self._skip_response_format:
                kwargs.pop("response_format", None)
            result = self._raw_completion(model, messages, kwargs)
            # Some grammar-constrained backends (notably LM Studio fronting a
            # "thinking" model like qwen3.x) return EMPTY content under a
            # response_format JSON schema — the schema decoder yields nothing. The
            # prompt already asks for JSON and the parser is lenient, so drop the
            # schema and retry once: the model then emits the findings as normal
            # (fenced) text we can parse. Remember it so later calls skip it too.
            if not result.text.strip() and kwargs.get("response_format") is not None:
                self._skip_response_format = True
                kwargs.pop("response_format")
                result = self._raw_completion(model, messages, kwargs)
            return result

        result = _call()
        return result.model_copy(update={"attempts": attempts})

    def _raw_completion(
        self, model: str, messages: list[Message], kwargs: dict[str, Any]
    ) -> ProviderResult:
        """One litellm call, with the temperature-value-rejection fallback applied."""
        try:
            response = litellm.completion(model=model, messages=messages, **kwargs)
        except Exception as exc:
            if "temperature" not in kwargs or not _rejects_temperature(exc):
                raise
            # Drop temperature for this and every subsequent retry, letting the
            # model use its only supported value (its default).
            kwargs.pop("temperature")
            response = litellm.completion(model=model, messages=messages, **kwargs)
        return self._map_response(response, model)

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
        if not self.prompt_cache or not _supports_cache_control(model):
            return _merge_user_messages(messages)

        out = list(messages)
        cumulative = 0
        sys_index = next((i for i, m in enumerate(out) if m.get("role") == "system"), None)
        if sys_index is not None and isinstance(out[sys_index].get("content"), str):
            sys_text = out[sys_index]["content"]
            cumulative = _count_tokens(sys_text)
            if cumulative >= _MIN_CACHEABLE_TOKENS:
                sys_marked: Any = [_cache_block(sys_text)]
                out[sys_index] = {**out[sys_index], "content": sys_marked}

        run_start, run = _trailing_user_run(out)
        if len(run) >= 2:
            # Split shape: every user message but the last is shared prefix.
            prefix_texts = [str(m.get("content", "")) for m in run[:-1]]
            cumulative += sum(_count_tokens(t) for t in prefix_texts)
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
        )


def _cache_block(text: str) -> dict[str, Any]:
    """A text content block carrying an ephemeral cache breakpoint."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


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
    forwards ``cache_control`` (Anthropic direct; Bedrock, where it becomes a
    Converse ``cachePoint``), and litellm's model-capability map must say the
    model itself caches. Any lookup failure (an unknown or freshly released
    model) means "don't mark" — caching is an optimisation, never worth an error.
    """
    if not model.startswith(_CACHE_CONTROL_PREFIXES):
        return False
    try:
        return bool(supports_prompt_caching(model=model))
    except Exception:
        return False


def _cache_usage(usage: Any) -> tuple[int, int]:
    """Extract (cache_read, cache_creation) token counts from a usage object.

    Anthropic/Bedrock report ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens``; OpenAI-style responses report reads under
    ``prompt_tokens_details.cached_tokens``. All optional — absent means 0.
    """
    cache_read = getattr(usage, "cache_read_input_tokens", None)
    if not cache_read:
        details = getattr(usage, "prompt_tokens_details", None)
        cache_read = getattr(details, "cached_tokens", None) if details is not None else None
    cache_creation = getattr(usage, "cache_creation_input_tokens", None)
    return int(cache_read or 0), int(cache_creation or 0)


def _count_tokens(text: str) -> int:
    """Token count for the minimum-cacheable-block check.

    Reuses the engine's cached tiktoken encoder (len/4 fallback) — imported
    lazily so building a provider never drags the engine in at import time.
    """
    from lgtmaybe.engine.compress import count_tokens

    return count_tokens(text)
