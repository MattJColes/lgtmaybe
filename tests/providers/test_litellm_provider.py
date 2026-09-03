"""Tests for LiteLLMProvider — the litellm adapter.

All tests monkeypatch litellm.completion at the boundary so no real network
calls are made.
"""

from __future__ import annotations

import contextlib
import io
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import litellm
import pytest
from pydantic import BaseModel

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

    def test_an_empty_response_retry_keeps_the_first_call_s_price(self) -> None:
        """The empty body was generated and billed before the re-send; a call
        recovered here must report the whole bill, not just the retry's part."""
        empty = _fake_response(content="", prompt_tokens=1000, completion_tokens=200)
        good = _fake_response(
            content=json.dumps({"findings": []}), prompt_tokens=1000, completion_tokens=200
        )
        with (
            patch("litellm.completion", side_effect=[empty, good]),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                side_effect=[(0.003, 0.002), (0.001, 0.001)],
            ),
        ):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-4-5"
            )

        assert result.cost_usd == pytest.approx(0.007)

    def test_a_fallback_run_carries_the_primary_s_tab(self) -> None:
        """The primary's requests were issued and billed before the rescue, so
        the rescue's result reports the whole bill — the cost rule mirrors the
        attempts rule it sits beside."""
        truncated = _fake_response(
            prompt_tokens=1000, completion_tokens=4096, finish_reason="length"
        )
        good = _fake_response(prompt_tokens=1000, completion_tokens=200)
        with (
            patch("litellm.completion", side_effect=[truncated, good]),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                side_effect=[(0.050, 0.000), (0.001, 0.001)],
            ),
        ):
            provider = LiteLLMProvider(fallback_model="openai/gpt-4o")
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-4-5"
            )

        assert result.cost_usd == pytest.approx(0.052)
        assert result.attempts >= 2

    def test_a_double_failure_merges_both_costs(self) -> None:
        """Primary truncated, fallback truncated: the error the profiler reads
        must charge for both, or the costliest run shape underreports itself."""
        first = _fake_response(prompt_tokens=1000, completion_tokens=4096, finish_reason="length")
        second = _fake_response(prompt_tokens=1000, completion_tokens=4096, finish_reason="length")
        with (
            patch("litellm.completion", side_effect=[first, second]),
            patch(
                "lgtmaybe.providers.litellm_provider.cost_per_token",
                side_effect=[(0.050, 0.000), (0.010, 0.010)],
            ),
        ):
            provider = LiteLLMProvider(fallback_model="openai/gpt-4o")
            with pytest.raises(ProviderTruncated) as excinfo:
                provider.complete(
                    [{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-4-5"
                )

        assert excinfo.value.cost_usd == pytest.approx(0.070)


def _tool_call_response(arguments: str, name: str = "lgtmaybe_structured_output") -> Any:
    """A reply that answered through the forced schema tool, content empty."""
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=[call]), finish_reason="tool_calls"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


class _Schema(BaseModel):
    findings: list[str]


class TestRouteSchemaSupport:
    """The schema is chosen for the ROUTE before the first call, not learned from
    a 400 that never comes.

    litellm runs with `drop_params`, which strips every OpenAI-vocabulary param
    a route's capability list omits — silently. The zai route lists `tools`
    but not `response_format`, so the schema vanished on the way out, the model
    answered prose, and the adapter still reported that structured output was
    on. Neither of the two existing recoveries (a 400 naming the field, an
    empty body) can see a param litellm removed before the request left.
    """

    _WITH_TOOLS = ["max_tokens", "temperature", "tools", "tool_choice"]
    _BARE = ["max_tokens", "temperature"]

    def test_a_route_that_drops_response_format_gets_the_schema_as_a_tool(self) -> None:
        sent: list[dict[str, Any]] = []

        def capture(**kwargs: Any) -> Any:
            sent.append(kwargs)
            return _tool_call_response('{"findings": ["x"]}')

        with (
            patch("litellm.get_supported_openai_params", return_value=self._WITH_TOOLS),
            patch("litellm.completion", side_effect=capture),
        ):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "zai/glm-4.6", response_format=_Schema
            )

        assert result.text == '{"findings": ["x"]}'
        assert len(sent) == 1, "decided up front — no wasted round-trip first"
        assert "response_format" not in sent[0]
        assert sent[0]["tools"][0]["function"]["name"] == "lgtmaybe_structured_output"
        assert sent[0]["tool_choice"]["function"]["name"] == "lgtmaybe_structured_output"
        # Enforcement was kept, by another mechanism — not given up.
        assert provider.sends_response_format("zai/glm-4.6")
        assert not provider.schema_dropped()

    def test_a_route_with_no_structured_output_mechanism_drops_the_schema_up_front(self) -> None:
        sent: list[dict[str, Any]] = []

        def capture(**kwargs: Any) -> Any:
            sent.append(kwargs)
            return _fake_response('{"findings": []}')

        with (
            patch("litellm.get_supported_openai_params", return_value=self._BARE),
            patch("litellm.completion", side_effect=capture),
        ):
            provider = LiteLLMProvider()
            provider.complete(
                [{"role": "user", "content": "hi"}], "some/route", response_format=_Schema
            )

        assert len(sent) == 1
        assert "response_format" not in sent[0] and "tools" not in sent[0]
        # Given up, and said so: the engine reads this to name the downgrade.
        assert provider.schema_dropped()
        assert not provider.sends_response_format("some/route")

    def test_a_route_that_takes_the_schema_sends_it_unchanged(self) -> None:
        sent: list[dict[str, Any]] = []

        def capture(**kwargs: Any) -> Any:
            sent.append(kwargs)
            return _fake_response('{"findings": []}')

        with (
            patch(
                "litellm.get_supported_openai_params",
                return_value=[*self._WITH_TOOLS, "response_format"],
            ),
            patch("litellm.completion", side_effect=capture),
        ):
            LiteLLMProvider().complete(
                [{"role": "user", "content": "hi"}], "openai/gpt-5", response_format=_Schema
            )

        assert sent[0]["response_format"] is _Schema
        assert "tools" not in sent[0]

    def test_a_capability_lookup_failure_leaves_the_schema_on(self) -> None:
        """Unknown route ⇒ trial by request, exactly as before: the 400 path
        still recovers, and a lookup error must never cost enforcement."""
        sent: list[dict[str, Any]] = []

        def capture(**kwargs: Any) -> Any:
            sent.append(kwargs)
            return _fake_response('{"findings": []}')

        def unknown(**kwargs: Any) -> Any:
            raise ValueError("unknown provider")

        with (
            patch("litellm.get_supported_openai_params", side_effect=unknown),
            patch("litellm.completion", side_effect=capture),
        ):
            LiteLLMProvider().complete(
                [{"role": "user", "content": "hi"}], "mystery/model", response_format=_Schema
            )

        assert sent[0]["response_format"] is _Schema

    def test_the_lookup_is_made_once_per_model(self) -> None:
        calls: list[str] = []

        def lookup(**kwargs: Any) -> Any:
            calls.append(kwargs["model"])
            return self._WITH_TOOLS

        with (
            patch("litellm.get_supported_openai_params", side_effect=lookup),
            patch("litellm.completion", return_value=_tool_call_response('{"findings": []}')),
        ):
            provider = LiteLLMProvider()
            for _ in range(3):
                provider.complete(
                    [{"role": "user", "content": "hi"}], "zai/glm-4.6", response_format=_Schema
                )

        assert calls == ["zai/glm-4.6"]


class TestContextWindowExceeded:
    """A prompt the model's window cannot hold is a payload problem, not a
    permanent one: the identical request can never succeed, but a smaller one
    can, and the engine owns the split. litellm names the case with its own
    exception type; the adapter hands it up under the port's name rather than
    letting it fall through as a generic bad request the engine would give up on.
    """

    @staticmethod
    def _too_long() -> Exception:
        from litellm.exceptions import ContextWindowExceededError

        return ContextWindowExceededError(
            message="prompt is too long: 214000 tokens > 200000 maximum",
            model="claude-sonnet-4-5",
            llm_provider="anthropic",
        )

    def test_surfaces_as_input_too_large_after_one_request(self) -> None:
        from lgtmaybe.core.models import is_unrecoverable
        from lgtmaybe.core.ports import ProviderInputTooLarge

        with patch("litellm.completion", side_effect=self._too_long()) as completion:
            with pytest.raises(ProviderInputTooLarge, match="too long") as info:
                LiteLLMProvider().complete([{"role": "user", "content": "hi"}], "anthropic/x")

        # Never retried in place: the same prompt is the same size.
        assert completion.call_count == 1
        # But not unrecoverable either — a smaller request is the remedy, and
        # the engine must not be told this call's failure is beyond saving.
        assert not is_unrecoverable(info.value)

    def test_is_handed_to_the_caller_when_it_owns_the_remedy(self) -> None:
        """With `defer_truncation` the engine holds the batch, so the adapter
        must not spend the fallback model on the identical oversized prompt."""
        from lgtmaybe.core.ports import ProviderInputTooLarge

        with patch("litellm.completion", side_effect=self._too_long()) as completion:
            provider = LiteLLMProvider(fallback_model="anthropic/bigger")
            with pytest.raises(ProviderInputTooLarge):
                provider.complete(
                    [{"role": "user", "content": "hi"}], "anthropic/x", defer_truncation=True
                )

        assert completion.call_count == 1
