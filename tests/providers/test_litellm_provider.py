"""Tests for LiteLLMProvider — the litellm adapter.

All tests monkeypatch litellm.completion at the boundary so no real network
calls are made.
"""

from __future__ import annotations

import contextlib
import io
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import litellm
import pytest

from lgtmaybe.core.models import ProviderResult
from lgtmaybe.core.ports import ProviderTruncated
from lgtmaybe.providers.litellm_provider import LiteLLMProvider


def _fake_response(
    content: str = "hello",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    finish_reason: str | None = None,
    usage_extra: dict[str, Any] | None = None,
) -> Any:
    """Build a minimal litellm ModelResponse lookalike."""
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        **(usage_extra or {}),
    )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        usage=usage,
    )


class TestLiteLLMProvider:
    def test_complete_returns_provider_result_with_text(self) -> None:
        response = _fake_response(content="some text")
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.text == "some text"

    def test_null_content_maps_to_empty_string(self) -> None:
        """A model that returns null content (e.g. answered only via a reasoning
        channel under JSON mode) maps to "" rather than crashing downstream."""
        response = _fake_response(content=None)
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.text == ""

    def test_complete_maps_token_counts_from_usage(self) -> None:
        response = _fake_response(prompt_tokens=50, completion_tokens=100)
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.input_tokens == 50
        assert result.output_tokens == 100

    def test_complete_reports_reasoning_tokens_on_the_success_path(self) -> None:
        """Reasoning tokens must be visible on calls that SUCCEEDED, not only on
        the ones that blew the ceiling.

        Read only from the failure path, the number can never answer the
        question it exists to answer: a truncated call has reasoning + findings
        >= max_tokens by definition, so that sample has no healthy call to
        compare against. The success path is where the comparison lives.
        """
        response = _fake_response(prompt_tokens=900, completion_tokens=1200)
        response.usage.completion_tokens_details = SimpleNamespace(reasoning_tokens=1100)
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openrouter/deepseek")

        assert result.reasoning_tokens == 1100
        # Reasoning is a SUBSET of the completion count, never an addition to it —
        # adding it to `output_tokens` would double-count against the budget.
        assert result.output_tokens == 1200

    def test_a_route_without_reasoning_detail_reports_unknown(self) -> None:
        """No detail is not an error, and it is not a zero either: "the route
        never said" and "the model thought nothing" send a reader to opposite
        conclusions about whether the ceiling has headroom."""
        with patch("litellm.completion", return_value=_fake_response()):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.reasoning_tokens is None

    def test_complete_passes_messages_and_model_to_litellm(self) -> None:
        response = _fake_response()
        messages = [{"role": "user", "content": "review this"}]
        with patch("litellm.completion", return_value=response) as mock_completion:
            provider = LiteLLMProvider()
            provider.complete(messages, "openai/gpt-4o")

        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args
        assert (
            call_kwargs.kwargs["model"] == "openai/gpt-4o" or call_kwargs.args[0] == "openai/gpt-4o"
        )
        assert messages in call_kwargs.args or call_kwargs.kwargs.get("messages") == messages

    def test_complete_passes_extra_opts_to_litellm(self) -> None:
        response = _fake_response()
        with patch("litellm.completion", return_value=response) as mock_completion:
            provider = LiteLLMProvider()
            provider.complete(
                [{"role": "user", "content": "hi"}],
                "openai/gpt-4o",
                api_key="sk-test",
            )

        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs.get("api_key") == "sk-test"

    def test_complete_sets_timeout_on_litellm_call(self) -> None:
        response = _fake_response()
        with patch("litellm.completion", return_value=response) as mock_completion:
            provider = LiteLLMProvider()
            provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        call_kwargs = mock_completion.call_args.kwargs
        assert "timeout" in call_kwargs
        assert call_kwargs["timeout"] > 0

    def test_result_is_provider_result_instance(self) -> None:
        response = _fake_response()
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert isinstance(result, ProviderResult)

    def test_drop_params_is_enabled_for_unsupported_provider_params(self) -> None:
        """Importing the provider enables litellm.drop_params so a model that
        rejects ``temperature``/``response_format`` (e.g. bedrock
        ``openai.gpt-5.5``) drops them instead of failing the whole review,
        while models that support them keep them."""
        assert litellm.drop_params is True

    def test_litellm_never_prints_its_banner_to_stdout(self) -> None:
        """litellm prints a "Give Feedback / Get Help" + "LiteLLM.Info:" banner
        straight to stdout when it maps a provider error. On ``lgtmaybe review
        --json`` that lands in front of the findings array and breaks
        ``json.load`` on the output — and the banner text contains a ``[``, so
        even a naive "find the first bracket" recovery picks the wrong one.
        Importing the provider suppresses it; the errors themselves still
        surface, through the exception and our own stderr logging."""
        assert litellm.suppress_debug_info is True

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), pytest.raises(litellm.BadRequestError):
            # A real (offline) litellm call through its error-mapping path — the
            # code that does the printing. A flag assertion alone would still
            # pass if litellm moved the banner behind a different switch.
            litellm.completion(model="no-such-provider/x", messages=[{"role": "user", "x": "hi"}])

        assert captured.getvalue() == ""


class TestCostEstimation:
    """The adapter prices each call with litellm's own pricing map.

    A profile that reports tokens but not money answers only half the question
    a metered run asks. The map is authoritative where it knows a model and
    raises where it does not — but a few ids it half-knows price silently at
    zero, and ollama is genuinely free, so a zero is never reported as a price:
    silence, not $0.00, because those are different claims.
    """

    def test_complete_stamps_the_priced_cost(self) -> None:
        response = _fake_response(prompt_tokens=1000, completion_tokens=200)
        with (
            patch("litellm.completion", return_value=response),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                return_value=(0.003, 0.002),
            ) as price,
        ):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-4-5"
            )

        assert result.cost_usd == pytest.approx(0.005)
        assert price.call_args.kwargs["model"] == "anthropic/claude-sonnet-4-5"
        assert price.call_args.kwargs["prompt_tokens"] == 1000
        assert price.call_args.kwargs["completion_tokens"] == 200

    def test_pricing_sees_the_cache_breakdown(self) -> None:
        """Cache reads bill at a discount, so a cache-aware call priced without
        its cache numbers overstates the cost on every cached lens."""
        response = _fake_response(
            prompt_tokens=1000,
            completion_tokens=200,
            usage_extra={"cache_read_input_tokens": 900, "cache_creation_input_tokens": 100},
        )
        with (
            patch("litellm.completion", return_value=response),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                return_value=(0.001, 0.002),
            ) as price,
        ):
            provider = LiteLLMProvider()
            provider.complete([{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-4-5")

        assert price.call_args.kwargs["cache_read_input_tokens"] == 900
        assert price.call_args.kwargs["cache_creation_input_tokens"] == 100

    def test_unmapped_pricing_reports_no_cost(self) -> None:
        """A model the pricing map has never heard of raises; an estimate must
        never fail the call that produced it."""
        response = _fake_response(prompt_tokens=1000, completion_tokens=200)
        with (
            patch("litellm.completion", return_value=response),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                side_effect=Exception("This model isn't mapped yet."),
            ),
        ):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-5.5")

        assert result.cost_usd is None

    def test_a_silent_zero_price_reports_no_cost(self) -> None:
        """The map prices some half-known ids (an unversioned bedrock id) at a
        silent $0.00. A price of zero is indistinguishable from that bug and
        from genuinely-free ollama — neither is a price, so neither is reported."""
        response = _fake_response(prompt_tokens=1000, completion_tokens=200)
        with (
            patch("litellm.completion", return_value=response),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                return_value=(0.0, 0.0),
            ),
        ):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "bedrock/anthropic.claude-sonnet-4-5"
            )

        assert result.cost_usd is None

    def test_a_truncation_carries_its_cost(self) -> None:
        """A ceiling hit is routinely the costliest call in a run, and it fails —
        so its price rides on the exception, where the failure is recorded."""
        response = _fake_response(
            prompt_tokens=1000,
            completion_tokens=4096,
            finish_reason="length",
        )
        with (
            patch("litellm.completion", return_value=response),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                return_value=(0.003, 0.002),
            ),
        ):
            provider = LiteLLMProvider()
            with pytest.raises(ProviderTruncated) as excinfo:
                provider.complete(
                    [{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-4-5"
                )

        assert excinfo.value.cost_usd == pytest.approx(0.005)
