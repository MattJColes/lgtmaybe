"""Tests for the provider factory — maps (Provider, model) -> litellm model string."""

from __future__ import annotations

import logging

import pytest

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.factory import build_provider, litellm_model_string


class TestLiteLLMModelString:
    def test_openai_prefix(self) -> None:
        assert litellm_model_string(Provider.openai, "gpt-4o") == "openai/gpt-4o"

    def test_anthropic_prefix(self) -> None:
        assert (
            litellm_model_string(Provider.anthropic, "claude-3-haiku-20240307")
            == "anthropic/claude-3-haiku-20240307"
        )

    def test_openrouter_prefix(self) -> None:
        assert (
            litellm_model_string(Provider.openrouter, "meta-llama/llama-3-70b-instruct")
            == "openrouter/meta-llama/llama-3-70b-instruct"
        )

    def test_bedrock_prefix(self) -> None:
        assert (
            litellm_model_string(Provider.bedrock, "anthropic.claude-3-haiku-20240307-v1:0")
            == "bedrock/anthropic.claude-3-haiku-20240307-v1:0"
        )

    def test_vertex_prefix(self) -> None:
        assert (
            litellm_model_string(Provider.vertex, "gemini-2.0-flash")
            == "vertex_ai/gemini-2.0-flash"
        )

    def test_ollama_prefix(self) -> None:
        assert litellm_model_string(Provider.ollama, "llama2") == "ollama/llama2"

    def test_azure_prefix(self) -> None:
        assert litellm_model_string(Provider.azure, "gpt-4o") == "azure/gpt-4o"

    def test_openai_compatible_uses_openai_prefix(self) -> None:
        # litellm routes any OpenAI-compatible server through the openai prefix +
        # a custom api_base (DeepSeek, llama.cpp, LM Studio, vLLM).
        assert (
            litellm_model_string(Provider.openai_compatible, "deepseek-chat")
            == "openai/deepseek-chat"
        )


class TestBuildProvider:
    def test_build_provider_returns_litellm_provider(self) -> None:
        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        provider = build_provider(Provider.openai, "gpt-4o", api_key="sk-test")
        assert isinstance(provider, LiteLLMProvider)

    def test_build_provider_openai_carries_api_key(self) -> None:
        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        provider = build_provider(Provider.openai, "gpt-4o", api_key="sk-test")
        assert isinstance(provider, LiteLLMProvider)
        assert provider.default_opts.get("api_key") == "sk-test"

    def test_build_provider_ollama_carries_api_base(self) -> None:
        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        provider = build_provider(Provider.ollama, "llama2", api_base="http://localhost:11434")
        assert isinstance(provider, LiteLLMProvider)
        assert provider.default_opts.get("api_base") == "http://localhost:11434"

    def test_build_provider_azure_carries_api_key_and_base(self) -> None:
        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        provider = build_provider(
            Provider.azure,
            "gpt-4o",
            api_key="azure-secret",
            api_base="https://my-resource.openai.azure.com",
        )
        assert isinstance(provider, LiteLLMProvider)
        assert provider.default_opts.get("api_key") == "azure-secret"
        assert provider.default_opts.get("api_base") == "https://my-resource.openai.azure.com"
        assert provider.model == "azure/gpt-4o"

    def test_build_provider_azure_keyless_carries_ad_token(self) -> None:
        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        provider = build_provider(
            Provider.azure,
            "gpt-4o",
            api_base="https://my-resource.openai.azure.com",
            azure_ad_token="ad-token-xyz",
        )
        assert isinstance(provider, LiteLLMProvider)
        assert provider.default_opts.get("azure_ad_token") == "ad-token-xyz"
        assert provider.default_opts.get("api_base") == "https://my-resource.openai.azure.com"
        assert "api_key" not in provider.default_opts

    def test_build_provider_openai_compatible_carries_key_and_base(self) -> None:
        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        provider = build_provider(
            Provider.openai_compatible,
            "deepseek-chat",
            api_key="sk-deepseek",
            api_base="https://api.deepseek.com/v1",
        )
        assert isinstance(provider, LiteLLMProvider)
        assert provider.default_opts.get("api_key") == "sk-deepseek"
        assert provider.default_opts.get("api_base") == "https://api.deepseek.com/v1"
        assert provider.model == "openai/deepseek-chat"

    def test_build_provider_openai_compatible_gets_the_long_local_timeout(self) -> None:
        # The endpoint may be a slow local server (llama.cpp / LM Studio / vLLM),
        # so default to the generous timeout — overridable for fast cloud endpoints.
        provider = build_provider(
            Provider.openai_compatible,
            "deepseek-chat",
            api_key="sk-x",
            api_base="https://api.deepseek.com/v1",
        )
        assert provider.default_opts.get("timeout") == 1800

    def test_openai_compatible_timeout_is_overridable(self) -> None:
        provider = build_provider(
            Provider.openai_compatible,
            "deepseek-chat",
            api_key="sk-x",
            api_base="https://api.deepseek.com/v1",
            timeout=30,
        )
        assert provider.default_opts.get("timeout") == 30

    def test_build_provider_stores_resolved_model_string(self) -> None:
        provider = build_provider(Provider.bedrock, "anthropic.claude-3-haiku-20240307-v1:0")
        assert provider.model == "bedrock/anthropic.claude-3-haiku-20240307-v1:0"

    def test_build_provider_resolves_fallback_model(self) -> None:
        provider = build_provider(Provider.ollama, "qwen3:27b", fallback_model="llama2")
        assert provider.fallback_model == "ollama/llama2"

    def test_build_provider_threads_timeout_into_default_opts(self) -> None:
        provider = build_provider(Provider.ollama, "llama2", timeout=600)
        assert provider.default_opts.get("timeout") == 600

    def test_ollama_gets_a_long_default_timeout_when_unset(self) -> None:
        provider = build_provider(Provider.ollama, "llama2")
        assert provider.default_opts.get("timeout") == 1800

    def test_cloud_gets_a_short_default_timeout_when_unset(self) -> None:
        provider = build_provider(Provider.openai, "gpt-4o", api_key="sk-test")
        assert provider.default_opts.get("timeout") == 600

    def test_explicit_timeout_overrides_the_provider_default(self) -> None:
        provider = build_provider(Provider.ollama, "llama2", timeout=45)
        assert provider.default_opts.get("timeout") == 45

    def test_ollama_leaves_thinking_to_ollama(self) -> None:
        """Send nothing, because either literal is wrong for some model.

        Ollama's chat route defaults thinking ON for a thinking-capable model
        when the field is unset, and 400s if a non-thinking model is asked for
        it. Pinning False switched reasoning off for every local reasoning
        model; pinning True would break every non-reasoning one.
        """
        provider = build_provider(Provider.ollama, "qwen3.6:35b", api_base="http://localhost:11434")
        assert "think" not in provider.default_opts

    def test_cloud_does_not_set_think(self) -> None:
        provider = build_provider(Provider.openai, "gpt-4o", api_key="sk-test")
        assert "think" not in provider.default_opts

    def test_ollama_sets_a_large_num_ctx(self) -> None:
        # The default ollama context (~4k) truncates real review prompts; the
        # default is sized to hold a real multi-file diff.
        provider = build_provider(Provider.ollama, "qwen3.6:35b", api_base="http://localhost:11434")
        assert provider.default_opts.get("num_ctx", 0) >= 32768

    def test_ollama_num_ctx_is_overridable(self) -> None:
        provider = build_provider(
            Provider.ollama, "qwen3.6:35b", api_base="http://localhost:11434", num_ctx=32768
        )
        assert provider.default_opts.get("num_ctx") == 32768

    def test_cloud_does_not_set_num_ctx(self) -> None:
        provider = build_provider(Provider.openai, "gpt-4o", api_key="sk-test")
        assert "num_ctx" not in provider.default_opts


class TestDefaultTimeout:
    def test_ollama_default_is_longer_than_cloud(self) -> None:
        from lgtmaybe.providers.factory import default_timeout_for

        assert default_timeout_for(Provider.ollama) > default_timeout_for(Provider.openai)

    def test_openai_compatible_default_matches_ollama(self) -> None:
        # Both can front a slow local model, so they share the generous default.
        from lgtmaybe.providers.factory import default_timeout_for

        assert default_timeout_for(Provider.openai_compatible) == default_timeout_for(
            Provider.ollama
        )

    def test_openrouter_default_matches_ollama(self) -> None:
        # openrouter is a gateway to arbitrary models, including reasoning models
        # that routinely think well past a cloud-sized budget — it gets the
        # generous default too.
        from lgtmaybe.providers.factory import default_timeout_for

        assert default_timeout_for(Provider.openrouter) == default_timeout_for(Provider.ollama)

    def test_every_provider_default_clears_ten_minutes(self) -> None:
        """No provider may default to a budget a reasoning model can blow through:
        a 60s-class default is what turned real reviews into 'call failed'
        notices with zero findings."""
        from lgtmaybe.providers.factory import default_timeout_for

        assert all(default_timeout_for(p) >= 600 for p in Provider)

    def test_adapter_fallback_is_never_tighter_than_a_provider_default(self) -> None:
        """The adapter's last-resort timeout applies whenever a caller builds a
        provider outside the factory (or passes timeout=None through), so it must
        not silently reimpose a budget shorter than the factory would have."""
        from lgtmaybe.providers.factory import CLOUD_TIMEOUT, default_timeout_for

        # One definition, shared: >= would let the two 600s drift apart silently.
        assert CLOUD_TIMEOUT == min(default_timeout_for(p) for p in Provider)

    def test_build_provider_threads_temperature_into_default_opts(self) -> None:
        provider = build_provider(Provider.ollama, "llama2", temperature=0.0)
        assert provider.default_opts.get("temperature") == 0.0

    def test_factory_provider_calls_litellm_with_resolved_model(self) -> None:
        """The engine passes the raw cfg.model; the call must still use the
        factory-resolved model string (regression for 'LLM Provider NOT provided')."""
        from types import SimpleNamespace
        from unittest.mock import patch

        provider = build_provider(Provider.ollama, "qwen3:27b", api_base="http://localhost:11434")
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

        with patch("litellm.completion", return_value=response) as mock_completion:
            provider.complete([{"role": "user", "content": "hi"}], model="qwen3:27b")

        assert mock_completion.call_args.kwargs["model"] == "ollama/qwen3:27b"


class TestParamSupport:
    """A configured param the model will not honour is named, not swallowed.

    litellm runs with ``drop_params`` on (so one unsupported param can't fail a
    whole review), which means a param its capability map doesn't recognise for
    a model is discarded with no warning at all. Every case below is judged
    against litellm's REAL map, except the one that says it stubs it.
    """

    def test_an_unsupported_param_is_named_at_build_time(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """gpt-4o has no reasoning channel, so litellm drops ``reasoning_effort``.
        The run has to say so — a silently dropped knob is indistinguishable from
        a knob that did not work."""
        with caplog.at_level(logging.WARNING, logger="lgtmaybe.providers.factory"):
            build_provider(Provider.openai, "gpt-4o", api_key="k", reasoning_effort="low")

        assert any("reasoning_effort" in str(record.__dict__) for record in caplog.records)

    def test_a_supported_param_is_not_warned_about(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="lgtmaybe.providers.factory"):
            build_provider(Provider.openai, "gpt-4o", api_key="k", max_tokens=8192)

        assert caplog.records == []

    def test_a_provider_native_option_is_not_warned_about(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``num_ctx`` is ollama's own option, not an OpenAI-vocabulary param, so
        litellm's map never lists it and it is not litellm's to drop. Warning
        about it would only train the reader to ignore the warning."""
        with caplog.at_level(logging.WARNING, logger="lgtmaybe.providers.factory"):
            build_provider(Provider.ollama, "llama2", num_ctx=32768)

        assert caplog.records == []

    def test_the_check_is_not_a_hardcoded_param_list(self) -> None:
        """Keyed off litellm's capability map, so a param nobody wrote a special
        case for is covered too — which is the whole point of the mechanism."""
        from lgtmaybe.providers.factory import dropped_params

        assert dropped_params(Provider.anthropic, "claude-3-haiku-20240307", ["logprobs"]) == [
            "logprobs"
        ]

    def test_a_non_openai_option_is_never_reported_as_dropped(self) -> None:
        """Only OpenAI-vocabulary params are judged; anything else rides through
        to the provider as a passthrough."""
        from lgtmaybe.providers.factory import dropped_params

        assert dropped_params(Provider.ollama, "llama2", ["num_ctx", "think"]) == []


class TestOpenRouterReasoning:
    """OpenRouter's native ``reasoning`` field, for models litellm's map misses."""

    def test_unmapped_model_gets_the_native_reasoning_field(self) -> None:
        """litellm only forwards ``reasoning_effort`` on openrouter for a model
        its capability map flags reasoning-capable — which the newest models are
        not. Those are exactly the models a reasoning budget gets set for, so the
        budget goes out in OpenRouter's own top-level ``reasoning`` object."""
        built = build_provider(Provider.openrouter, "vendor/unmapped-model", reasoning_effort="low")

        assert built.default_opts["extra_body"] == {"reasoning": {"effort": "low"}}
        assert "reasoning_effort" not in built.default_opts

    def test_a_natively_forwarded_model_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OpenRouter rejects a request carrying BOTH fields with ``400 Only one
        of "reasoning" and "reasoning_effort" may be provided`` — so when litellm
        will forward the flat param itself, nothing may be injected beside it.
        The capability map is stubbed here so the guarantee does not depend on a
        particular model staying in litellm's map across a dependency bump."""
        import litellm

        real = litellm.get_supported_openai_params
        monkeypatch.setattr(
            litellm,
            "get_supported_openai_params",
            lambda **kw: [*(real(**kw) or []), "reasoning_effort"],
        )
        built = build_provider(
            Provider.openrouter, "vendor/reasoning-model", reasoning_effort="low"
        )

        assert built.default_opts.get("reasoning_effort") == "low"
        assert "extra_body" not in built.default_opts

    def test_a_value_with_no_native_equivalent_is_reported_not_translated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``default`` is in litellm's normalised set but not in OpenRouter's
        ``reasoning.effort`` enum (none/minimal/low/medium/high/xhigh). Sending a
        nearby value instead would quietly buy a budget nobody asked for."""
        with caplog.at_level(logging.WARNING, logger="lgtmaybe.providers.factory"):
            built = build_provider(
                Provider.openrouter, "vendor/unmapped-model", reasoning_effort="default"
            )

        assert "extra_body" not in built.default_opts
        assert any("reasoning_effort" in str(record.__dict__) for record in caplog.records)

    def test_the_native_field_survives_litellms_own_transformation(self) -> None:
        """One layer past our own kwargs: litellm's openrouter transformation
        assigns its own `extra_body` (for `transforms`/`models`/`route`), so a
        bump that made that assignment clobber rather than merge would put us
        back where we started — a budget that looks sent and never leaves."""
        import litellm

        built = build_provider(Provider.openrouter, "vendor/unmapped-model", reasoning_effort="low")
        outbound = litellm.get_optional_params(
            model="vendor/unmapped-model",
            custom_llm_provider="openrouter",
            drop_params=True,
            extra_body=built.default_opts["extra_body"],
        )

        assert outbound["extra_body"]["reasoning"] == {"effort": "low"}

    def test_no_other_route_is_reshaped(self) -> None:
        """Scoped to openrouter: every other route keeps the request shape it has
        always sent."""
        built = build_provider(Provider.openai, "gpt-4o", api_key="k", reasoning_effort="low")

        assert "extra_body" not in built.default_opts


class TestQueuedTimeoutScaling:
    """A local server may run one request at a time while lgtmaybe issues the
    whole fan-out at once. Every queued call's timeout clock starts when the
    request is *sent*, not when the server picks it up, so the last call in a
    six-wide fan-out can burn its entire budget waiting its turn and time out
    having never been served. Scaling the default by the fan-out width is what
    keeps the budget a budget for *work* rather than for work-plus-queue.
    """

    def test_a_local_default_scales_with_the_fan_out_width(self) -> None:
        from lgtmaybe.providers.factory import default_timeout_for

        one = default_timeout_for(Provider.ollama, concurrency=1)
        six = default_timeout_for(Provider.ollama, concurrency=6)
        assert six == one * 6

    def test_openai_compatible_scales_too(self) -> None:
        # llama.cpp and vLLM front the same single-box failure mode.
        from lgtmaybe.providers.factory import default_timeout_for

        assert default_timeout_for(Provider.openai_compatible, concurrency=4) == (
            default_timeout_for(Provider.openai_compatible, concurrency=1) * 4
        )

    def test_a_hosted_provider_does_not_scale(self) -> None:
        """Hosted endpoints serve concurrent requests rather than queueing them,
        so there is no queue time to cover — scaling there would only delay the
        moment a genuinely stuck call is reported."""
        from lgtmaybe.providers.factory import default_timeout_for

        for hosted in (Provider.openai, Provider.anthropic, Provider.bedrock):
            assert default_timeout_for(hosted, concurrency=6) == default_timeout_for(hosted)

    def test_openrouter_does_not_scale(self) -> None:
        """A gateway is hosted capacity, not one box — it gets the generous
        default for slow *models*, not for a queue it does not have."""
        from lgtmaybe.providers.factory import default_timeout_for

        assert default_timeout_for(Provider.openrouter, concurrency=6) == default_timeout_for(
            Provider.openrouter
        )

    def test_serial_local_is_unchanged(self) -> None:
        """The mitigation must be free when there is no queue: width 1 is the
        pre-existing number, byte for byte."""
        from lgtmaybe.providers.factory import _SLOW_TIMEOUT, default_timeout_for

        assert default_timeout_for(Provider.ollama, concurrency=1) == _SLOW_TIMEOUT
        assert default_timeout_for(Provider.ollama) == _SLOW_TIMEOUT

    def test_an_explicit_timeout_is_never_scaled(self) -> None:
        """`timeout: 600` means 600. Scaling a number the user chose would make
        the setting mean something other than what it says."""
        provider = build_provider(Provider.ollama, "qwen3.6:35b", timeout=600, concurrency=6)
        assert provider.default_opts["timeout"] == 600

    def test_build_provider_applies_the_scaled_default(self) -> None:
        from lgtmaybe.providers.factory import _SLOW_TIMEOUT

        provider = build_provider(Provider.ollama, "qwen3.6:35b", concurrency=6)
        assert provider.default_opts["timeout"] == _SLOW_TIMEOUT * 6
