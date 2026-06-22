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
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from lgtmaybe.core.models import ProviderResult
from lgtmaybe.core.ports import Message, ProviderClient

_DEFAULT_TIMEOUT = 60  # seconds
_MAX_ATTEMPTS = 4

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


def _is_quota_rate_limit(exc: BaseException) -> bool:
    """True for a quota/billing 429 (permanent), False for a capacity 429."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _QUOTA_MARKERS)


def _is_permanent(exc: BaseException) -> bool:
    """True when retrying *exc* cannot plausibly succeed, so we should not."""
    if isinstance(exc, _PERMANENT_ERROR_TYPES):
        return True
    if isinstance(exc, RateLimitError):
        return _is_quota_rate_limit(exc)
    return False


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
        **default_opts: Any,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model
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
        @retry(
            retry=retry_if_exception(lambda exc: not _is_permanent(exc)),
            stop=stop_after_attempt(_MAX_ATTEMPTS),
            wait=wait_exponential_jitter(initial=0.1, max=5),
            reraise=True,
        )
        def _call() -> ProviderResult:
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

        return _call()

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

    def _map_response(self, response: Any, model: str) -> ProviderResult:
        # Some providers return null content (e.g. a model that answered only via
        # a reasoning channel under JSON mode); treat that as empty, not a crash.
        text: str = response.choices[0].message.content or ""
        input_tokens: int = response.usage.prompt_tokens
        output_tokens: int = response.usage.completion_tokens

        return ProviderResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
