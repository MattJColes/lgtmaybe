"""LiteLLMProvider: the litellm adapter implementing ProviderClient.

Wraps litellm.completion with retry (tenacity), an explicit timeout, and an
optional fallback model.
"""

from __future__ import annotations

import copy
import hashlib
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
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

# litellm.utils re-exports this without listing it in __all__, so mypy rejects
# the re-export — import it from the module that defines it, like the
# exceptions above.
from litellm.llms.base_llm.base_utils import type_to_response_format_param
from litellm.utils import supports_prompt_caching
from pydantic import BaseModel
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
    Provider,
    ProviderResult,
    attempts_of,
    stamp_attempts,
    stamp_unrecoverable,
)
from lgtmaybe.core.ports import (
    Message,
    ProviderTruncated,
    ProviderWallTimeout,
)
from lgtmaybe.providers.factory import CLOUD_TIMEOUT, litellm_model_string

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
# The number that matters is the CUMULATIVE wait, not how long any one of them
# looks. The first version of this started at 5s and so waited 5s, 10s, 20s —
# each of them obviously "slow", and all four attempts still landing inside 36
# seconds, i.e. inside the very minute that had just refused them. It was the
# same bug it replaced, one order of magnitude out instead of two, and it read
# as correct because every individual wait did.
#
# Starting at 20s puts the attempts at roughly 20s, 60s and 120s: past the first
# window, and past the second. `_RATE_LIMIT_BACKOFF_MAX` caps the growth so a
# fourth-attempt wait cannot run away.
#
# Both ladders stay well inside the call's existing `stop_before_delay` budget
# (2.5 × timeout — 1,500s on direct cloud, 4,500s on the slow-capable routes
# openrouter/ollama/openai-compatible), and that stop weighs the wait ABOUT to be
# taken, so a backoff that would blow the budget ends the call instead.
_RATE_LIMIT_BACKOFF_INITIAL = 20.0
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


def _header_sources(exc: BaseException) -> tuple[Mapping[str, str], ...]:
    """Every place litellm may have put *exc*'s response headers.

    Three of them, because litellm's own ``_get_response_headers`` looks in the
    same three: the exception's ``headers`` attribute, the ``response`` it wraps,
    and the ``litellm_response_headers`` its exception mapper stamps on the way
    out. Which one is populated depends on the route.
    """
    return tuple(
        source
        for source in (
            getattr(exc, "headers", None),
            getattr(getattr(exc, "response", None), "headers", None),
            getattr(exc, "litellm_response_headers", None),
        )
        if source
    )


def _header(exc: BaseException, name: str) -> str | None:
    """*name*'s value from any source that carries it, whatever its casing.

    Two mistakes are worth naming, because both read as correct and both lose the
    header in exactly the case it exists for:

    - **Stopping at the first non-empty source.** That is what litellm's helper
      does, but its job is "give me the headers" and ours is "find this one" — an
      exception holding an unrelated ``headers`` dict alongside a response with
      the real hint would answer from the wrong mapping and find nothing.
    - **Matching a fixed spelling.** ``httpx.Headers`` is case-insensitive, so
      two hard-coded spellings look sufficient; litellm's ``headers=`` kwarg is a
      plain ``Dict[str, str]``, where the lookup is exact and ``RETRY-AFTER``
      misses.

    So: search every source, compare case-insensitively, first match wins.
    """
    wanted = name.lower()
    for source in _header_sources(exc):
        try:
            items = source.items()
        except AttributeError:  # pragma: no cover — a mapping-shaped non-mapping
            continue
        for key, value in items:
            if str(key).lower() == wanted:
                return value
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
    raw = _header(exc, "Retry-After")
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


# Phrases a backend uses to say "I do not know this request field". Matched
# loosely because the wording is the backend's, not litellm's.
_REJECTION_PHRASES = ("not permitted", "not supported", "unsupported", "unknown", "unexpected")

# A self-hosted OpenAI-compatible server refuses a tool call by naming the
# start-up flag it is missing, not by calling the field unsupported — so these
# stand on their own rather than needing a phrase from the tuple above. Specific
# enough that a genuine 400 (context length, bad model) cannot match.
_TOOL_PARSER_PHRASES = ("--enable-auto-tool-choice", "tool-call-parser")


def _rejects_field(exc: Exception, *names: str) -> bool:
    """True when *exc* reads as "this route does not take <one of names>"."""
    msg = str(exc).lower()
    return any(name in msg for name in names) and any(p in msg for p in _REJECTION_PHRASES)


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
    rejection phrase. A false positive costs one re-send in tool mode (see
    :func:`_schema_tool_kwargs`); a false negative costs the whole review.
    """
    return _rejects_field(exc, "response_format", "output_config", "responseformat")


def _rejects_tool_config(exc: Exception) -> bool:
    """True when an API error means the route won't take the tool schema either.

    The second half of :func:`_rejects_response_format`: a route that refuses
    ``response_format`` is asked for the same schema as a forced tool call
    instead, and a route that refuses *that* has genuinely no structured-output
    mechanism left. Bedrock names the Converse field (``toolConfig``); the
    OpenAI-shaped routes name ``tools`` / ``tool_choice``.

    A self-hosted OpenAI-compatible server refuses differently: it names the
    server flag it was started without rather than calling the field
    unsupported. vLLM answers ``"auto" tool choice requires
    --enable-auto-tool-choice and --tool-call-parser to be set``, which carries
    none of ``_REJECTION_PHRASES`` — so without these the 400 read as permanent
    and killed the review rather than degrading to prompt-instructed JSON.
    """
    if any(phrase in str(exc).lower() for phrase in _TOOL_PARSER_PHRASES):
        return True
    return _rejects_field(exc, "toolconfig", "tool_choice", "toolchoice", "tools")


# The one tool we ever offer. Named, not anonymous, because ``tool_choice`` has
# to point at it by name to make the call forced rather than optional — an
# optional tool lets the model answer in prose and enforces nothing.
_SCHEMA_TOOL_NAME = "lgtmaybe_structured_output"


def _json_schema_of(response_format: Any) -> dict[str, Any] | None:
    """The JSON Schema inside a ``response_format``, or None if there isn't one.

    Two shapes reach here: the pydantic model the engine passes (litellm derives
    the schema from it) and litellm's own ``{"type": "json_schema",
    "json_schema": {"schema": …}}`` dict. Anything else — ``{"type":
    "json_object"}``, say — constrains nothing we could put in a tool's
    parameters, so there is no tool call worth making.
    """
    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        return response_format.model_json_schema()
    if isinstance(response_format, Mapping):
        nested = response_format.get("json_schema")
        schema = nested.get("schema") if isinstance(nested, Mapping) else None
        if isinstance(schema, Mapping):
            return dict(schema)
    return None


_BEDROCK_PREFIX = "bedrock/"

# Bedrock's structured-output validator accepts only a subset of JSON Schema:
# the numeric-bound keywords pydantic emits for ``Field(ge=…, le=…)`` —
# ``ReviewFinding.line``'s ``minimum``, ``confidence``'s ``minimum``/``maximum``
# — are "Extra inputs" that 400 the whole request before the model ever runs
# (issue #531). The bounds are re-checked by the same pydantic model when the
# reply is parsed, so nothing is enforced less by not sending them.
_SCHEMA_BOUND_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")

# Keys whose value maps property NAMES to schemas. The strip descends into
# their values but never pops keys off the map itself: a field literally named
# "minimum" is a field, not a keyword.
_SCHEMA_NAME_MAPS = ("properties", "$defs", "definitions", "patternProperties")


def _strip_numeric_bounds(schema: dict[str, Any]) -> None:
    """Remove the numeric-bound keywords from *schema*, in place, recursively."""
    for key in _SCHEMA_BOUND_KEYS:
        schema.pop(key, None)
    for key, value in schema.items():
        if key in _SCHEMA_NAME_MAPS and isinstance(value, dict):
            children: Iterable[Any] = value.values()
        elif isinstance(value, list):
            children = value
        else:
            children = (value,)
        for child in children:
            if isinstance(child, dict):
                _strip_numeric_bounds(child)


def _bedrock_wire_response_format(response_format: Any) -> Any:
    """*response_format* as Bedrock's validator will take it, or unchanged.

    A pydantic model is converted through litellm's own
    ``type_to_response_format_param`` — the exact dict litellm would derive from
    the class one layer down, so the request differs only by the stripped
    keywords — and a dict shape is deep-copied before the strip, since the
    caller's ``response_format`` is reused across the lens fan-out. Applied per
    effective model on the bedrock route ONLY: every other route keeps the
    class, because each litellm transformation knows its own schema dialect
    (vertex deliberately derives a compact ``$ref`` schema from the class that a
    pre-converted strict dict would deny it). A shape carrying no schema —
    ``{"type": "json_object"}``, say — has nothing to strip and goes out as it
    came.
    """
    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        converted: Any = type_to_response_format_param(response_format)
    elif isinstance(response_format, Mapping):
        converted = copy.deepcopy(dict(response_format))
    else:
        return response_format
    nested = converted.get("json_schema") if isinstance(converted, Mapping) else None
    schema = nested.get("schema") if isinstance(nested, Mapping) else None
    if not isinstance(schema, dict):
        return response_format
    _strip_numeric_bounds(schema)
    return converted


def _schema_tool_kwargs(response_format: Any) -> dict[str, Any] | None:
    """The same schema expressed as a forced tool call, or None if it can't be.

    A route refusing ``response_format`` has not necessarily refused *structured
    output*. Bedrock's Converse endpoint implements it as tool use — a
    ``toolConfig`` whose ``inputSchema`` is the shape you want back, plus a
    ``toolChoice`` naming that tool — which is exactly what litellm's
    OpenAI-shaped ``tools`` / ``tool_choice`` translate into. So the recovery
    from a rejected ``response_format`` is to ask for the same schema through
    the mechanism the route does implement, before giving up on enforcement and
    falling back to prompt-instructed JSON.

    That matters most on the newest models — the ones litellm's capability map
    doesn't know yet, so ``drop_params`` leaves the unsupported field on and the
    service 400s the whole review.
    """
    schema = _json_schema_of(response_format)
    if schema is None:
        return None
    return {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": _SCHEMA_TOOL_NAME,
                    "description": "Return the result. Call this exactly once, with the "
                    "whole answer as its arguments.",
                    "parameters": schema,
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": _SCHEMA_TOOL_NAME}},
    }


def _is_schema_tool(tools: Any) -> bool:
    """Whether *tools* is the one tool :func:`_schema_tool_kwargs` builds.

    The adapter owns only the tool it added. Everything that reacts to tool mode
    — stripping it, re-sending without it, reading the answer out of it — asks
    this first, so a caller's own ``tools`` are never retired, overwritten, or
    mistaken for our schema. Nothing in lgtmaybe passes tools today; this keeps
    the adapter honest for anything that later does.
    """
    if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], Mapping):
        return False
    function = tools[0].get("function")
    return isinstance(function, Mapping) and function.get("name") == _SCHEMA_TOOL_NAME


def _tool_call_arguments(message: Any) -> str:
    """Our schema tool's arguments, or "" when the reply didn't call it.

    Under a forced tool call the answer rides in the arguments and ``content`` is
    empty, so a reader that only ever looks at ``content`` reports every lens as
    having returned nothing. Matched by name rather than taking the first call of
    any kind: a reply that answered in ``content`` and called something else on
    the side must not have that call read as its answer.
    """
    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        if getattr(function, "name", None) != _SCHEMA_TOOL_NAME:
            continue
        arguments = getattr(function, "arguments", None)
        if isinstance(arguments, str) and arguments.strip():
            return arguments
    return ""


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


class LiteLLMProvider:
    """ProviderClient backed by litellm with retry and optional fallback."""

    def __init__(
        self,
        *,
        model: str = "",
        fallback_model: str | None = None,
        effort_override_supported: bool = True,
        **default_opts: Any,
    ) -> None:
        if "prompt_cache" in default_opts:
            raise TypeError("prompt_cache was removed; prompt caching is always enabled")
        self.model = model
        self.fallback_model = fallback_model
        # Whether this model's capability entry will actually carry a
        # `reasoning_effort` we invent for a step-down retry. Defaults True and
        # is only ever set False when litellm's map POSITIVELY omits the param:
        # the map does not know the newest models, which are exactly the ones
        # that truncate on reasoning, so silence has to mean "try it" or the
        # retry would be withheld from the models it exists for.
        self._effort_override_supported = effort_override_supported
        self.default_opts: dict[str, Any] = default_opts
        # Models that have proved they won't take response_format — either by
        # 400ing on it or by decoding it to nothing (see _call). Their later
        # calls skip it up front instead of paying a wasted round-trip first.
        #
        # Keyed by MODEL, not by provider instance. One instance serves the
        # primary and the fallback, and the triage/review/reflect slots share
        # its credentials, so an instance-wide flag let a rejection by any one
        # of them silently downgrade the others to prompt-instructed JSON —
        # a quality regression on the strong model triggered by a model it
        # never ran.
        self._schema_dropped: set[str] = set()
        # Models whose schema now travels as a forced tool call instead (see
        # `_schema_tool_kwargs`). Disjoint from `_schema_dropped`: this set is
        # enforcement preserved by another mechanism, that one is enforcement
        # given up. Keyed by MODEL for the same reason.
        self._schema_tool: set[str] = set()
        # Memoized supports-cache-control answers: the review fans out many
        # completions on the same model string, and the capability lookup is a
        # pure function of it. Instance-scoped (not lru_cache) so tests that
        # patch the litellm lookup stay isolated.
        self._cache_capable: dict[str, bool] = {}

    def _disable_response_format(self, model: str, why: str) -> None:
        """Record — and announce — that *model* will not take the schema.

        Announced because the consequence is invisible otherwise: every later
        call for this model falls back to prompt-instructed JSON, and a model
        that then answers in prose surfaces as "every review lens returned
        unparseable output" with nothing connecting the two. This repo already
        holds itself to that rule for every other param (``provider.factory``
        names the ones litellm's map says will be discarded); the schema drop
        was the one exemption.

        Once per model, not once per lens: the fan-out sends the same shape N
        times and N identical warnings would bury the one that matters.
        """
        if model in self._schema_dropped:
            return
        self._schema_dropped.add(model)
        # Tool mode was enforcement; giving up on the schema retires it too.
        self._schema_tool.discard(model)
        _log.warning(
            "structured output disabled for this model — later calls send "
            "prompt-instructed JSON only, which a weaker model may not honour",
            extra={"model": model, "reason": why},
        )

    def _use_schema_tool(self, model: str, kwargs: dict[str, Any]) -> bool:
        """Swap this request's ``response_format`` for the equivalent tool call.

        Returns False when there is nothing to swap — no schema in the
        ``response_format`` (so the tool would enforce nothing), or the request
        is already in tool mode. False means the caller should fall through to
        the real downgrade.

        Announced at info, not warning, precisely because it is *not* the
        downgrade ``_disable_response_format`` announces: the model is still held
        to the schema, just through the mechanism its route implements. Once per
        model, like that warning — the lens fan-out sends the same shape N times.
        """
        tools = kwargs.get("tools")
        if tools is not None and not _is_schema_tool(tools):
            # The caller brought their own tools; swapping ours in would drop
            # them. Their request shape wins — fall through to the plain drop.
            return False
        tool_kwargs = _schema_tool_kwargs(kwargs.get("response_format"))
        if tool_kwargs is None:
            return False
        kwargs.pop("response_format", None)
        kwargs.update(tool_kwargs)
        if model not in self._schema_tool:
            self._schema_tool.add(model)
            _log.info(
                "structured output sent as a forced tool call for this model — "
                "the schema is still enforced",
                extra={"model": model},
            )
        return True

    @staticmethod
    def _strip_schema(kwargs: dict[str, Any]) -> None:
        """Remove every structured-output mechanism THIS ADAPTER added.

        The tool half is conditional: a caller's own ``tools`` are not ours to
        retire, so giving up on the schema must not disable unrelated tool use
        for the model for the rest of the run.
        """
        kwargs.pop("response_format", None)
        if _is_schema_tool(kwargs.get("tools")):
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)

    def drop_response_format(self, model: str, why: str) -> None:
        """Stop sending the schema for *model* — asked by the engine, not inferred.

        The two existing triggers are things the adapter can see for itself: a
        400 naming the param, and schema mode decoding to an empty string. The
        third cannot be seen from here at all — a reply that arrives non-empty
        and well-formed on the wire, and turns out not to be findings. Only the
        engine parses, so only the engine knows.

        Hence the first engine→adapter *setter*, where ``schema_dropped`` and
        ``lower_reasoning_effort`` are read-only probes. It stays off the frozen
        ``ProviderClient`` port for the same reason they do: the engine
        feature-detects it, and an adapter that cannot honour it simply never
        remembers.

        Keyed exactly as ``complete`` resolves the model, so the entry matches
        the one ``_call`` looks up — a factory-built provider carries the
        prefixed litellm string, and the engine only knows ``cfg.model``.
        """
        self._disable_response_format(self.model or model, why)

    def sends_response_format(self, model: str) -> bool:
        """Whether a call for *model* would actually carry the schema.

        The engine passes ``response_format`` on every lens call, but ``_call``
        strips it for a model already known to refuse it — so "the engine asked
        for a schema" and "a schema went out" are different facts. The
        schema-less re-run turns on the second: blaming enforcement that was
        never applied would re-send the request that just failed, byte for byte,
        which is precisely what that retry exists to avoid.

        Keyed as ``complete`` resolves the model, like ``drop_response_format``.
        """
        return (self.model or model) not in self._schema_dropped

    def schema_dropped(self) -> bool:
        """Whether any model lost ``response_format`` during this run.

        Adapter-only, beyond the frozen ``ProviderClient`` port, like
        ``lower_reasoning_effort`` above: the engine feature-detects it so it can
        name the downgrade in the review notice when calls also failed. A port
        method would oblige every fake and every future adapter to answer a
        question only this one can.
        """
        return bool(self._schema_dropped)

    def lower_reasoning_effort(self) -> dict[str, Any] | None:
        """Per-call opts that step this provider's reasoning effort down one level.

        ``None`` when there is nothing to step: a value already at the bottom of
        the ladder, or ``default``, which names no position on it and so cannot
        be moved down from.

        With NO effort configured the step is to ``_EFFORT_FLOOR`` rather than
        nowhere. That case used to answer ``None`` — to keep a run that never set
        an effort sending byte-identical requests — but it is the very
        configuration that produces this failure: a model reasoning at its own
        default is the one that spends a whole output ceiling thinking, and
        answering ``None`` left it the only model with no lever at all. The
        byte-identical guarantee is unaffected, because this is consulted ONLY
        after a reasoning-bound truncation: a healthy call never reaches here.

        Adapter-only, beyond the frozen ``ProviderClient`` port, and deliberately
        so: the effort lives in one of two provider-shaped places — a flat
        ``reasoning_effort``, or the nested ``reasoning`` object the factory
        re-routes it into for OpenRouter (see ``_honour_param_support``, where
        sending both is a 400). The engine asks for "one level less" and gets back
        opts in whichever shape this provider was built with; it never learns
        which.

        Read off ``default_opts`` because that is where the configured value was
        resolved, and returned as an override rather than mutated in place: this
        is one retry's request, not a new setting for the rest of the run.
        """
        flat = self.default_opts.get("reasoning_effort")
        if isinstance(flat, str):
            lower = _one_level_lower(flat)
            return {"reasoning_effort": lower} if lower else None
        raw_extra = self.default_opts.get("extra_body")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        nested = extra.get("reasoning")
        if isinstance(nested, dict) and isinstance(nested.get("effort"), str):
            lower = _one_level_lower(nested["effort"])
            if not lower:
                return None
            # The whole extra_body is replaced, not deep-merged: `complete` merges
            # per-call opts over the defaults a key at a time, so a partial
            # extra_body here would drop every other key it carries.
            return {"extra_body": {**extra, "reasoning": {**nested, "effort": lower}}}
        # Nothing configured in either shape, so the floor picks its own shape —
        # from the ROUTE, never from whether `extra_body` happens to exist, which
        # a caller may set for any unrelated provider option.
        #
        # OpenRouter gets the nested object for the same reason the factory
        # re-routes a configured effort into it: litellm forwards the flat param
        # only for models its capability map flags reasoning-capable, and the
        # newest models are not in that map — exactly the set that truncates this
        # way. A flat param there would be dropped and the retry would fail
        # identically. OpenRouter takes the nested object regardless of model.
        if self.model.startswith(_OPENROUTER_PREFIX):
            return {"extra_body": {**extra, "reasoning": {"effort": _EFFORT_FLOOR}}}
        # Flat param, so `drop_params` gets a say. A route whose capability entry
        # omits it would have the floor stripped and re-send the request that
        # just failed — billed twice for one answer. Report the original failure
        # instead; the engine already stops when this answers None.
        if not self._effort_override_supported:
            return None
        return {"reasoning_effort": _EFFORT_FLOOR}

    def escalate_model(self) -> str | None:
        """The resolved fallback model, for a caller that owns the escalation.

        Feature-detected from the engine exactly as ``lower_reasoning_effort`` is,
        and off the frozen :class:`ProviderClient` port for the same reason: which
        litellm model string a configured fallback resolves to is adapter
        knowledge, and a provider that has no second model should simply not
        answer rather than every fake in the suite growing a no-op.

        Pairs with the ``model_override`` option on :meth:`complete`. The engine
        asks whether an escalation is on offer, then spends it — rather than
        re-billing the primary to reach the adapter's own fallback branch.
        """
        return self.fallback_model

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        # Adapter-owned options, popped BEFORE the merge so neither can reach
        # litellm as a request param. Both exist for one caller — the engine —
        # which owns the recovery ordering for a lens call; see `escalate_model`.
        defer_truncation = bool(opts.pop("defer_truncation", False))
        model_override: str | None = opts.pop("model_override", None)
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
        merged.setdefault("prompt_cache_key", _prefix_cache_key(messages))
        # A factory-built provider carries the resolved litellm model string
        # (e.g. "ollama/qwen3:27b"); prefer it over the caller's raw cfg.model.
        # An explicit override outranks both: it is the caller naming a model
        # this adapter resolved for it, which is the escalation below run early.
        effective_model = model_override or self.model or model
        try:
            return self._complete_with_retry(messages, effective_model, **merged)
        except Exception as exc:
            if self.fallback_model is None:
                raise
            if model_override is not None:
                # This call IS the escalation. Falling back from it would send the
                # request to the model that just failed it — a second full-price
                # answer to a question already answered wrong.
                raise
            if defer_truncation and isinstance(exc, ProviderTruncated):
                # The caller asked to own this one. A truncation is the only
                # failure where somebody upstream has a *better* remedy than
                # switching model: the engine holds the batch and the token
                # counts, so it can shrink the payload or lower the thinking
                # budget — cheap, aimed at the cause the counts named, and still
                # on the model the user chose. All this adapter could do is
                # re-send the identical oversized request to a second model.
                # It still gets to, from `escalate_model`, once the cheap remedy
                # has had its go. Every other failure falls back here as before.
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
        # Same per-model scope: only a bedrock model needs the schema stripped
        # to the subset its validator takes, and a non-bedrock fallback must
        # keep the class for its own route's conversion.
        if model.startswith(_BEDROCK_PREFIX) and kwargs.get("response_format") is not None:
            kwargs["response_format"] = _bedrock_wire_response_format(kwargs["response_format"])
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
            # A prior call already settled how this model takes (or refuses) the
            # schema, so don't pay the wasted round-trip again — apply it up front.
            if model in self._schema_dropped:
                self._strip_schema(kwargs)
            elif model in self._schema_tool:
                self._use_schema_tool(model, kwargs)
            result = self._raw_completion(model, messages, kwargs, count_request)
            # An empty completion is never a valid answer: a lens that found
            # nothing still owes `{"findings": []}`. So retry once, whatever the
            # request carried.
            #
            # Some grammar-constrained backends (notably LM Studio fronting a
            # "thinking" model like qwen3.x) return EMPTY content under a
            # response_format JSON schema — the schema decoder yields nothing. A
            # route that accepts `tools` and then ignores them is the same dead
            # end (nothing in the content, no tool call to read instead). Where
            # the request carried one, the schema is the first suspect, so it is
            # dropped before the retry and remembered so later calls skip it.
            #
            # Where it carried no schema the retry still happens, and used not
            # to. That left the case with NO recovery anywhere: the engine
            # cannot reformat an empty body (`repair_findings` returns None on
            # one) and has no schema left to drop, so the lens failed outright —
            # seen in the field as "unparseable model output (empty)" on a run
            # where an earlier call had already disabled structured output. An
            # empty body carries no evidence that the request was at fault, and
            # re-issuing it is the only move left.
            #
            # One retry, never a loop: `_raw_completion` is called at most twice
            # here, so a model answering empty twice reports empty rather than
            # spending a third call.
            if not result.text.strip():
                if kwargs.get("response_format") is not None or _is_schema_tool(
                    kwargs.get("tools")
                ):
                    self._disable_response_format(model, "empty-response")
                    self._strip_schema(kwargs)
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
                    _completion_with_wall_timeout(model, messages, kwargs),
                    model,
                    _configured_ceiling(kwargs),
                )
            except Exception as exc:
                if not self._drop_rejected_param(exc, model, kwargs):
                    raise

    def _drop_rejected_param(self, exc: Exception, model: str, kwargs: dict[str, Any]) -> bool:
        """Strip the one request param *exc* says this model won't take.

        Two params are sent for review quality rather than necessity —
        ``temperature`` (determinism) and ``response_format`` (structured output)
        — and a model that refuses either would otherwise fail the whole review
        over a preference. Both refusals are permanent 400s, so the recovery is
        to drop the param and re-send; ``kwargs`` is the dict every later retry
        of this call reuses, so the drop sticks for them too.

        Structured output gets two recoveries rather than one, in order of how
        much they keep: a rejected ``response_format`` becomes the same schema as
        a forced tool call, and only a route that refuses *that* too falls back
        to prompt-instructed JSON.

        Returns True when something was dropped and the call is worth re-sending.
        """
        if "temperature" in kwargs and _rejects_temperature(exc):
            # The param is accepted, only our value isn't — let the model use its
            # own default.
            kwargs.pop("temperature")
            return True
        # Both branches remember the outcome for this model's later calls, not
        # just this one: the lens fan-out sends the same shape N times, and
        # without that every one of them pays its own rejected round-trip first.
        if kwargs.get("response_format") is not None and _rejects_response_format(exc):
            if not self._use_schema_tool(model, kwargs):
                self._disable_response_format(model, "rejected")
                kwargs.pop("response_format")
            return True
        if _is_schema_tool(kwargs.get("tools")) and _rejects_tool_config(exc):
            self._disable_response_format(model, "tool-rejected")
            self._strip_schema(kwargs)
            return True
        return False

    def _with_cache_control(self, messages: list[Message], model: str) -> list[Message]:
        """Return *messages* shaped for the model's caching route, when it pays.

        The engine sends the review prompt as a lens-independent system
        preamble, then the shared prefix (the wrapped diff, plus hints), then
        the lens-specific instruction. This adapter is where that shape meets
        each provider:

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
        - Everywhere else (no breakpoint route or capability lookup failure):
          consecutive user messages are merged into one plain string and no
          marker is attached.
        """
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

    def _map_response(
        self, response: Any, model: str, ceiling: int | None = None
    ) -> ProviderResult:
        # Some providers return null content (e.g. a model that answered only via
        # a reasoning channel under JSON mode); treat that as empty, not a crash.
        message = response.choices[0].message
        text: str = message.content or ""
        # In tool mode the answer IS the tool call's arguments and `content` holds
        # nothing (or a preamble). Prefer the arguments wherever a call came back:
        # they are the schema-enforced payload, where the content is whatever the
        # model said around it.
        text = _tool_call_arguments(message) or text
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
        # single finding.
        #
        # The advice deliberately does NOT say "raise `max_tokens`". Measured (see
        # .lgtmaybe.yml): the dominant truncation is a CONTENT runaway — ~961
        # tokens of thought against ~32,700 of output, zero findings salvaged, on
        # roughly one lens call in five — which no ceiling prevents. Raising it
        # only buys a larger wasted call: at 32k a single runaway was 80-93% of a
        # review's wall clock, at 8k it is ~95s. The engine already splits the
        # batch, so the reader's real lever is the model, not this number.
        #
        # Second test, for the routes that never say it. litellm maps an
        # unrecognised finish reason to `stop`, and ollama's route reports
        # nothing useful at all, so a call cut off at the cap arrives looking
        # like a clean finish — measured on a local benchmark as two lens calls
        # reporting exactly the configured 512 output tokens with an empty error
        # column, which had the tooling downstream counting zero truncations.
        # Spending the ceiling to the token is not a stopping point a model
        # chooses; it is what being cut off looks like from the outside. Only
        # ever applied against a ceiling WE set — with none configured there is
        # nothing to compare against and a long answer is just a long answer.
        if _finish_reason(response) == "length" or _spent_the_ceiling(output_tokens, ceiling):
            reasoning = _reasoning_tokens(usage)
            detail = f" ({reasoning} reasoning)" if reasoning else ""
            raise ProviderTruncated(
                f"response hit the {output_tokens}-token `max_tokens` ceiling{detail} before "
                "finishing — the batch is re-reviewed in smaller pieces automatically, so a "
                "lens that keeps doing it is usually generation instability in the model, "
                "which a higher ceiling makes more expensive rather than prevents",
                text=text,
                # The same two numbers as the message, carried as data: the engine
                # decides whether shrinking the payload can help from the ratio
                # between them, and re-reading them out of the prose above would be
                # parsing our own sentence. None, not 0, when the route reported no
                # breakdown — "it never said" must not read as "it thought nothing".
                reasoning_tokens=reasoning,
                output_tokens=output_tokens,
                # Not diagnosis but accounting: the prompt was sent and billed,
                # so the spend ceiling must see it (see profiling.record_error).
                input_tokens=input_tokens,
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
            # The denominator for that count. Carried from the request rather
            # than looked up later: a per-call `max_tokens` overrides the
            # provider's, and a share against the wrong ceiling is worse than no
            # share at all.
            output_ceiling=ceiling,
            # Which model answered, resolved. Stamped on every result, not only a
            # fallback's: "the primary answered" is a claim worth being able to
            # make, and a field that appeared only on rescues would leave the
            # common case unreadable rather than uninteresting.
            model=model,
        )


# The reasoning ladder, lowest first. litellm's normalised set minus `default`,
# which is a "let the route decide" sentinel rather than a rung — there is no
# telling what it is one level below.
_EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh")

# Where a step-down lands when NOTHING was configured to step down from — the
# case that produces this failure most often, since a model reasoning at its own
# default is precisely the one that spends a whole output ceiling thinking.
#
# `low` rather than `none` or `minimal`: a model whose thinking overran is still
# a reasoning model, and switching thought off entirely trades a truncated
# review for a worse one. `low` is also the rung every reasoning route
# understands, where the two below it are unevenly supported.
_EFFORT_FLOOR = "low"

# ``openrouter/`` — derived rather than spelled out, so it cannot drift from the
# route prefix the factory builds model strings with. The one route that reads a
# nested ``reasoning`` object.
_OPENROUTER_PREFIX = litellm_model_string(Provider.openrouter, "")


def _one_level_lower(effort: str) -> str | None:
    """The rung below *effort*, or None at (or off) the bottom of the ladder."""
    try:
        index = _EFFORT_LADDER.index(effort)
    except ValueError:
        return None
    return _EFFORT_LADDER[index - 1] if index else None


def _configured_ceiling(kwargs: dict[str, Any]) -> int | None:
    """The output ceiling this request was actually sent with, if any.

    Read off the request rather than off ``default_opts``: a per-call
    ``max_tokens`` overrides the one the provider was built with, and judging a
    256-token call against a 4,096 default would miss every ceiling hit.

    ``max_completion_tokens`` is the same ceiling under the newer OpenAI
    spelling, which litellm accepts either way — but it is only consulted when
    ``max_tokens`` is ABSENT, never when it is merely falsy. ``max_tokens: 0`` is
    the uncapped escape hatch, and falling through on it would re-impose a
    ceiling the caller explicitly turned off, then report a truncation against
    it.
    """
    if "max_tokens" in kwargs:
        ceiling: int | None = kwargs["max_tokens"]
        return ceiling
    return kwargs.get("max_completion_tokens")


def _spent_the_ceiling(output_tokens: int, ceiling: int | None) -> bool:
    """Whether this response generated all the way to the ceiling we configured.

    ``>=`` rather than ``==``: some routes count a token or two past the cap, and
    a ceiling hit reported as 513-of-512 is still a ceiling hit. A ``ceiling`` of
    None (or 0, the uncapped escape hatch) means we set no cap, so there is
    nothing to judge against and this never fires.
    """
    return ceiling is not None and ceiling > 0 and output_tokens >= ceiling


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


def _reasoning_tokens(usage: Any) -> int | None:
    """Output tokens the model spent thinking, or None when the route doesn't say.

    litellm normalises this onto ``completion_tokens_details.reasoning_tokens``
    for the routes that report it. Read defensively — a route that omits the
    wrapper, or fills it with a non-number, must not turn a truncation report
    into a crash.

    None rather than 0, because they are different claims: "it never said" and
    "it did no thinking" send a reader to different conclusions about whether the
    ceiling has headroom, and only one of them is knowable here.

    A route that DOES report the breakdown and puts 0 in it has said the second
    one, and that is kept: it is a measurement, and a non-reasoning model landing
    at 0% belongs in the table beside the ones that did not. Booleans are
    excluded explicitly — `True` is an `int` in Python, and a route filling the
    field with a flag would otherwise be read as "one reasoning token".
    """
    details = getattr(usage, "completion_tokens_details", None)
    value = getattr(details, "reasoning_tokens", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


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
