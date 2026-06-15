"""Tests for retry + fallback behaviour in LiteLLMProvider."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

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
