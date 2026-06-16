"""Tests for retry + fallback behaviour in LiteLLMProvider."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import litellm
import pytest

from lgtmaybe.providers.litellm_provider import LiteLLMProvider


def _fake_response(content: str = "ok") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=10),
    )


class TestRetry:
    def test_first_call_raises_then_retry_succeeds(self) -> None:
        """Provider retries on transient failure and returns the good result."""
        good_response = _fake_response("retried ok")
        call_count = 0

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            return good_response

        with patch("litellm.completion", side_effect=flaky):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.text == "retried ok"
        assert call_count == 2

    def test_all_retries_exhausted_raises(self) -> None:
        """When all retries are exhausted the error propagates."""
        with patch("litellm.completion", side_effect=RuntimeError("always fails")):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")


class TestTemperatureRejection:
    def test_retries_without_temperature_when_model_rejects_the_value(self) -> None:
        """Models like gpt-5.5 accept the temperature param but only the default
        value; on that rejection the provider retries without temperature."""
        good = _fake_response("ok without temperature")
        seen_temperatures: list[Any] = []

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            seen_temperatures.append(kwargs.get("temperature", "absent"))
            if "temperature" in kwargs:
                raise RuntimeError(
                    "litellm.BadRequestError: OpenAIException - Unsupported value: "
                    "'temperature' does not support 0 with this model. Only the "
                    "default (1) value is supported."
                )
            return good

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "openai/gpt-5.5", temperature=0.0
            )

        assert result.text == "ok without temperature"
        assert seen_temperatures == [0.0, "absent"]

    def test_unrelated_bad_request_is_not_swallowed(self) -> None:
        """A non-temperature error must still propagate, not be retried bare."""
        with patch("litellm.completion", side_effect=RuntimeError("invalid api key")):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError, match="invalid api key"):
                provider.complete(
                    [{"role": "user", "content": "hi"}], "openai/gpt-5.5", temperature=0.0
                )


class TestFailFastOnPermanentErrors:
    """Retrying a permanent error can't fix it — and stacked backoff over every
    lens turns an instant "out of credit" into many minutes of burned runner
    time (the gpt-5.5 quota failure that took ~13 min). Permanent errors must
    fail fast; only genuinely transient ones get the exponential backoff."""

    def test_quota_rate_limit_is_not_retried(self) -> None:
        """OpenAI's insufficient_quota 429 ("exceeded your current quota") is a
        billing dead-end — one attempt, then raise."""
        calls = 0

        def quota(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.RateLimitError(
                message=(
                    "litellm.RateLimitError: OpenAIException - You exceeded your "
                    "current quota, please check your plan and billing details."
                ),
                model="gpt-5.5",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=quota):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.RateLimitError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-5.5")

        assert calls == 1

    def test_transient_rate_limit_is_still_retried(self) -> None:
        """A plain capacity rate-limit (not quota) is transient — keep the
        exponential backoff and retry it."""
        good = _fake_response("ok after backoff")
        calls = 0

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise litellm.RateLimitError(
                    message="RateLimitError: OpenAIException - Rate limit reached for gpt-4o",
                    model="gpt-4o",
                    llm_provider="openai",
                )
            return good

        with patch("litellm.completion", side_effect=flaky):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.text == "ok after backoff"
        assert calls == 2

    def test_authentication_error_is_not_retried(self) -> None:
        """A bad key won't become good on a retry — fail fast."""
        calls = 0

        def bad_auth(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.AuthenticationError(
                message="AuthenticationError: invalid api key",
                model="gpt-4o",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=bad_auth):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.AuthenticationError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert calls == 1

    def test_quota_error_does_not_storm_the_fallback_either(self) -> None:
        """Fail-fast applies to the fallback leg too: each model is tried once,
        no per-model retry storm."""
        calls = 0

        def quota(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.RateLimitError(
                message="OpenAIException - You exceeded your current quota",
                model="m",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=quota):
            provider = LiteLLMProvider(fallback_model="openai/gpt-4o-mini")
            with pytest.raises(litellm.RateLimitError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-5.5")

        assert calls == 2  # primary once + fallback once

    def test_ollama_connection_error_is_retried(self) -> None:
        """ollama gets the same treatment: a transient connection error (the
        local server still warming up) is retried, not failed fast."""
        good = _fake_response("ok")
        calls = 0

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise litellm.APIConnectionError(
                    message="Connection refused",
                    model="ollama/qwen3",
                    llm_provider="ollama",
                )
            return good

        with patch("litellm.completion", side_effect=flaky):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "ollama/qwen3")

        assert result.text == "ok"
        assert calls == 2

    def test_litellm_internal_retries_are_disabled(self) -> None:
        """We own retries via tenacity; litellm's own retry loop is switched off
        so a failure isn't ground through two stacked backoff layers."""
        response = _fake_response()
        with patch("litellm.completion", return_value=response) as mock_completion:
            provider = LiteLLMProvider()
            provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert mock_completion.call_args.kwargs.get("num_retries") == 0


class TestFallback:
    def test_primary_fails_hard_fallback_model_is_used(self) -> None:
        """After primary exhausts retries, fallback model is tried."""
        fallback_response = _fake_response("fallback result")

        primary_model = "openai/gpt-4o"
        fallback_model = "openai/gpt-3.5-turbo"

        called_with_models: list[str] = []

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            model = kwargs.get("model", args[0] if args else "")
            called_with_models.append(model)
            if model == primary_model:
                raise RuntimeError("primary dead")
            return fallback_response

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider(fallback_model=fallback_model)
            result = provider.complete([{"role": "user", "content": "hi"}], primary_model)

        assert result.text == "fallback result"
        assert fallback_model in called_with_models

    def test_no_fallback_configured_raises_on_hard_failure(self) -> None:
        """Without a fallback, hard failure propagates."""
        with patch("litellm.completion", side_effect=RuntimeError("dead")):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")
