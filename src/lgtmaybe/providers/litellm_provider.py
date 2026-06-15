"""LiteLLMProvider: the litellm adapter implementing ProviderClient.

Wraps litellm.completion with retry (tenacity), an explicit timeout, and an
optional fallback model.
"""

from __future__ import annotations

from typing import Any

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from lgtmaybe.core.models import ProviderResult
from lgtmaybe.core.ports import Message, ProviderClient

_DEFAULT_TIMEOUT = 60  # seconds
_MAX_ATTEMPTS = 4


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

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        merged = {**self.default_opts, **opts}
        merged.setdefault("timeout", _DEFAULT_TIMEOUT)
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
            stop=stop_after_attempt(_MAX_ATTEMPTS),
            wait=wait_exponential_jitter(initial=0.1, max=5),
            reraise=True,
        )
        def _call() -> ProviderResult:
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

        return _call()

    def _map_response(self, response: Any, model: str) -> ProviderResult:
        text: str = response.choices[0].message.content
        input_tokens: int = response.usage.prompt_tokens
        output_tokens: int = response.usage.completion_tokens

        return ProviderResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
