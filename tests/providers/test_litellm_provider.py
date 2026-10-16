"""Tests for LiteLLMProvider — the litellm adapter.

All tests monkeypatch litellm.completion at the boundary so no real network
calls are made.
"""

from __future__ import annotations

import contextlib
import io
import json
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch

import litellm
import pytest
from pydantic import BaseModel

from lgtmaybe.core.models import ProviderResult, schema_dropped_on, temperature_dropped_on
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
        assert not result.schema_dropped

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
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "some/route", response_format=_Schema
            )

        assert len(sent) == 1
        assert "response_format" not in sent[0] and "tools" not in sent[0]
        # Given up, and said so: the engine reads this to name the downgrade.
        assert result.schema_dropped
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

    def test_a_failed_call_reports_the_schema_it_had_already_dropped(self) -> None:
        """The drop is decided before the request, so a call that then fails has
        still lost its schema, and that is the likeliest cause of the failure.
        The exception carries it, since there is no result to carry it on."""

        def fail(**kwargs: Any) -> Any:
            raise litellm.BadRequestError(
                message="the model is unavailable", model="some/route", llm_provider="x"
            )

        with (
            patch("litellm.get_supported_openai_params", return_value=self._BARE),
            patch("litellm.completion", side_effect=fail),
            pytest.raises(litellm.BadRequestError) as exc_info,
        ):
            LiteLLMProvider().complete(
                [{"role": "user", "content": "hi"}], "some/route", response_format=_Schema
            )

        assert schema_dropped_on(exc_info.value)
        assert not temperature_dropped_on(exc_info.value)

    def test_a_call_that_never_asked_for_a_schema_drops_none(self) -> None:
        """`structured_output: false` sends no schema, so nothing was lost."""
        with (
            patch("litellm.get_supported_openai_params", return_value=self._BARE),
            patch("litellm.completion", return_value=_fake_response('{"findings": []}')),
        ):
            result = LiteLLMProvider().complete([{"role": "user", "content": "hi"}], "some/route")

        assert not result.schema_dropped

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


class TestRouteSchemaSupportUnderConcurrency:
    def test_every_concurrent_first_call_gets_the_tool(self) -> None:
        """The lens fan-out hits one instance at once. A lens arriving while a
        sibling's first call is still being shaped must reach the same shape,
        not find a half-recorded state and send the schema litellm then drops."""
        import threading
        import time

        sent: list[dict[str, Any]] = []
        lock = threading.Lock()

        def slow_lookup(**kwargs: Any) -> Any:
            time.sleep(0.05)  # long enough for every thread to be inside complete()
            return ["max_tokens", "temperature", "tools", "tool_choice"]

        def capture(**kwargs: Any) -> Any:
            with lock:
                sent.append(kwargs)
            return _tool_call_response('{"findings": []}')

        provider = LiteLLMProvider()
        errors: list[BaseException] = []

        def one_lens() -> None:
            try:
                provider.complete(
                    [{"role": "user", "content": "hi"}], "zai/glm-4.6", response_format=_Schema
                )
            except BaseException as exc:  # noqa: BLE001 — surfaced below
                errors.append(exc)

        with (
            patch("litellm.get_supported_openai_params", side_effect=slow_lookup),
            patch("litellm.completion", side_effect=capture),
        ):
            threads = [threading.Thread(target=one_lens) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert not errors
        assert len(sent) == 6
        assert all("response_format" not in kwargs and "tools" in kwargs for kwargs in sent)

    def test_a_route_that_gave_the_schema_up_is_not_re_armed_by_the_list(self) -> None:
        """Once the tool itself was refused, the route's list saying "tools" must
        not put the tool back on the next call."""
        sent: list[dict[str, Any]] = []

        def capture(**kwargs: Any) -> Any:
            sent.append(kwargs)
            if "tools" in kwargs:
                raise RuntimeError("BadRequestError: toolConfig is not supported for this model")
            return _fake_response('{"findings": []}')

        with (
            patch(
                "litellm.get_supported_openai_params",
                return_value=["max_tokens", "tools", "tool_choice"],
            ),
            patch("litellm.completion", side_effect=capture),
        ):
            provider = LiteLLMProvider()
            for _ in range(2):
                provider.complete(
                    [{"role": "user", "content": "hi"}], "some/route", response_format=_Schema
                )

        # Tool, refused; bare re-send; then the second call goes bare up front.
        assert [("tools" in k, "response_format" in k) for k in sent] == [
            (True, False),
            (False, False),
            (False, False),
        ]


class TestTemperatureReach:
    """``drop_params`` strips a temperature the route will not take, silently.

    The determinism default is the knob that vanishes that way — on Bedrock
    Converse models flagged ``supports_sampling_params: false``, on OpenAI
    gpt-5.x while reasoning is active, on Claude models that removed sampling —
    so the adapter has to say when what it configured never reached the model.
    These run against litellm's bundled model map (see ``tests/conftest.py``),
    because the map is what decides the drop.
    """

    _MESSAGES: ClassVar[list[Any]] = [{"role": "user", "content": "hi"}]

    def _run(self, model: str, **opts: Any) -> tuple[ProviderResult, list[dict[str, Any]]]:
        sent: list[dict[str, Any]] = []

        def capture(**kwargs: Any) -> Any:
            sent.append(kwargs)
            return _fake_response('{"findings": []}')

        with patch("litellm.completion", side_effect=capture):
            result = LiteLLMProvider(model=model, **opts).complete(self._MESSAGES, "ignored")
        return result, sent

    def test_a_bedrock_model_without_sampling_params_is_flagged(self) -> None:
        result, sent = self._run("bedrock/us.openai.gpt-5.5", temperature=0.0)

        assert result.temperature_dropped
        # Disclosure only: the request still carries exactly what it did before
        # — litellm is what strips it, and what this adapter hands it is unchanged.
        assert sent[0]["temperature"] == 0.0

    def test_a_gpt5_model_with_reasoning_active_is_flagged(self) -> None:
        result, _ = self._run("openai/gpt-5.5", temperature=0.0)
        assert result.temperature_dropped

    def test_a_gpt5_model_with_reasoning_off_is_not_flagged(self) -> None:
        """The one condition under which litellm forwards a non-1 temperature."""
        result, _ = self._run("openai/gpt-5.5", temperature=0.0, reasoning_effort="none")
        assert not result.temperature_dropped

    def test_a_claude_model_that_removed_sampling_is_flagged(self) -> None:
        result, _ = self._run("anthropic/claude-mythos-5", temperature=0.0)
        assert result.temperature_dropped

    @pytest.mark.parametrize(
        "model",
        [
            "openai/gpt-4o",
            "anthropic/claude-sonnet-4-5",
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            "ollama/qwen3:8b",
            "vertex_ai/gemini-2.5-pro",
        ],
    )
    def test_a_route_that_takes_the_temperature_is_not_flagged(self, model: str) -> None:
        result, sent = self._run(model, temperature=0.0)
        assert not result.temperature_dropped
        assert sent[0]["temperature"] == 0.0

    def test_no_configured_temperature_is_never_flagged(self) -> None:
        """Nothing was configured, so nothing was lost."""
        result, sent = self._run("bedrock/us.openai.gpt-5.5")
        assert not result.temperature_dropped
        assert "temperature" not in sent[0]

    def test_a_failed_lookup_is_not_flagged_and_never_fails_the_call(self) -> None:
        """Instrumentation must never be the reason a review does not run."""
        with patch("litellm.get_optional_params", side_effect=RuntimeError("map exploded")):
            result, sent = self._run("bedrock/us.openai.gpt-5.5", temperature=0.0)
        assert not result.temperature_dropped
        assert len(sent) == 1

    def test_an_unknown_route_is_not_flagged(self) -> None:
        result, _ = self._run("totally-unknown/xyz", temperature=0.0)
        assert not result.temperature_dropped

    def test_a_temperature_the_model_refused_is_flagged(self) -> None:
        """The other way it goes missing: the route forwards it and the model
        400s on the value, so the adapter re-sends without it."""

        def reject(**kwargs: Any) -> Any:
            if "temperature" in kwargs:
                raise RuntimeError(
                    "litellm.BadRequestError: Unsupported value: 'temperature' does not "
                    "support 0 with this model. Only the default (1) value is supported."
                )
            return _fake_response('{"findings": []}')

        with patch("litellm.completion", side_effect=reject):
            provider = LiteLLMProvider(model="openai/gpt-4o", temperature=0.0)
            first = provider.complete(self._MESSAGES, "ignored")
            # Remembered for the model, so a later call goes out without it too.
            later = provider.complete(self._MESSAGES, "ignored")

        assert first.temperature_dropped
        assert later.temperature_dropped

    def test_the_lookup_is_paid_once_per_model_not_once_per_lens(self) -> None:
        """The fan-out sends the same shape many times; the answer is a pure
        function of the route, the value and the effort."""
        real = litellm.get_optional_params
        with (
            patch("litellm.get_optional_params", side_effect=real) as lookup,
            patch("litellm.completion", return_value=_fake_response('{"findings": []}')),
        ):
            provider = LiteLLMProvider(model="bedrock/us.openai.gpt-5.5", temperature=0.0)
            results = [provider.complete(self._MESSAGES, "ignored") for _ in range(5)]

        assert all(result.temperature_dropped for result in results)
        assert lookup.call_count == 2, "with and without the temperature, once"

    def test_a_changed_reasoning_effort_re_derives_the_answer(self) -> None:
        """The effort is part of the memo key: the gpt-5 rule keeps the
        temperature at `none` and drops it at anything else, and the step-down
        retries change the effort mid-run. A memo keyed on the model alone would
        reuse the `none` answer for the `high` call and miss the drop.

        Each call is judged for itself, so a drop at `high` never colours a
        later call at `none`.
        """
        real = litellm.get_optional_params
        with (
            patch("litellm.get_optional_params", side_effect=real) as lookup,
            patch("litellm.completion", return_value=_fake_response('{"findings": []}')),
        ):
            provider = LiteLLMProvider(model="openai/gpt-5.5", temperature=0.0)
            high = provider.complete(self._MESSAGES, "ignored", reasoning_effort="high")
            none = provider.complete(self._MESSAGES, "ignored", reasoning_effort="none")

        assert lookup.call_count == 4, "both lookups, once per effort"
        assert high.temperature_dropped, "`high` drops it"
        assert not none.temperature_dropped, "`none` keeps the temperature"

    def test_the_build_site_seeds_the_record_through_the_public_surface(self) -> None:
        """The factory withholds the temperature before the client exists, so it
        records the loss on the adapter's public surface, and every call the
        client makes reports it: a builder reaching into a private method
        couples the two classes at an implementation detail."""
        provider = LiteLLMProvider(model="anthropic/claude-sonnet-4-5")

        provider.note_withheld_temperature("anthropic/claude-sonnet-4-5", "withheld-under-thinking")

        with patch("litellm.completion", return_value=_fake_response('{"findings": []}')):
            result = provider.complete(self._MESSAGES, "ignored")
        assert result.temperature_dropped

    def test_a_failed_call_reports_the_temperature_it_never_sent(self) -> None:
        """The strip is decided before the request, so a call that then fails
        lost its temperature all the same; the exception says so."""
        with (
            patch("litellm.completion", side_effect=RuntimeError("upstream exploded")),
            pytest.raises(RuntimeError) as exc_info,
        ):
            LiteLLMProvider(model="bedrock/us.openai.gpt-5.5", temperature=0.0).complete(
                self._MESSAGES, "ignored"
            )

        assert temperature_dropped_on(exc_info.value)

    def test_a_per_call_temperature_is_not_reported_withheld(self) -> None:
        """The build-time withhold is about the configured default; a caller
        that sends its own temperature on a call has it judged on its own."""
        provider = LiteLLMProvider(model="anthropic/claude-sonnet-4-5")
        provider.note_withheld_temperature("anthropic/claude-sonnet-4-5", "withheld-under-thinking")

        with patch("litellm.completion", return_value=_fake_response('{"findings": []}')):
            result = provider.complete(self._MESSAGES, "ignored", temperature=0.0)
        assert not result.temperature_dropped
