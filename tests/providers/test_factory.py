"""Tests for the provider factory — maps (Provider, model) -> litellm model string."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.factory import (
    build_provider,
    declared_effort_capability,
    litellm_model_string,
    resolve_max_tokens,
)


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
        assert litellm_model_string(Provider.ollama, "llama2") == "ollama_chat/llama2"

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

    def test_build_provider_azure_keyless_carries_ad_token_provider(self) -> None:
        """Keyless azure hands litellm a token PROVIDER, called per request, so a
        review that outlives one Entra token (~an hour) is not 401'd mid-run."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        def token_provider() -> str:
            return "ad-token-xyz"

        provider = build_provider(
            Provider.azure,
            "gpt-4o",
            api_base="https://my-resource.openai.azure.com",
            azure_ad_token_provider=token_provider,
        )
        assert isinstance(provider, LiteLLMProvider)
        assert provider.default_opts.get("api_base") == "https://my-resource.openai.azure.com"
        assert "api_key" not in provider.default_opts
        assert "azure_ad_token" not in provider.default_opts

        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        with patch("litellm.completion", return_value=response) as mock_completion:
            provider.complete([{"role": "user", "content": "hi"}], model="gpt-4o")

        sent = mock_completion.call_args.kwargs["azure_ad_token_provider"]
        assert sent() == "ad-token-xyz", "litellm must get a callable that fetches per request"
        assert "azure_ad_token" not in mock_completion.call_args.kwargs

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
        assert provider.fallback_model == "ollama_chat/llama2"

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

        assert mock_completion.call_args.kwargs["model"] == "ollama_chat/qwen3:27b"


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


class TestOutputCeiling:
    """A local model gets a finite output ceiling by default.

    Without one, a structured-output response that terminates badly decodes
    until the *timeout* stops it — 18 minutes of sustained GPU on a one-file
    diff, and hours for a full nine-lens review. The timeout is sized generously
    on purpose (a slow local model has to be given time to answer), which makes
    it the wrong instrument for a runaway decode: it can only notice half an
    hour late.
    """

    def test_ollama_gets_a_finite_ceiling(self) -> None:
        ceiling = resolve_max_tokens(Provider.ollama)
        assert ceiling is not None and ceiling > 0

    def test_the_ceiling_leaves_room_for_a_real_findings_payload(self) -> None:
        """Sized against the context window it shares rather than plucked from
        the air: an ollama review runs at `num_ctx` 32768, and a ceiling that
        crowded the prompt would truncate ordinary reviews to buy the fix."""
        from lgtmaybe.providers.factory import _OLLAMA_NUM_CTX

        ceiling = resolve_max_tokens(Provider.ollama)
        assert ceiling is not None
        assert 2048 <= ceiling <= _OLLAMA_NUM_CTX // 2

    def test_every_runaway_prone_route_gets_the_same_ceiling(self) -> None:
        """ollama is not the only route that decodes without terminating.

        Benchmark evidence: a local model behind `openai-compatible` emitted
        223,558 tokens on one lens call, and a hosted model on `openrouter`
        posted 699 false positives off a 393k-token response on a diff with
        nothing wrong in it. The assumption that a hosted API brings its own sane
        ceiling does not hold, so all three routes share one number.
        """
        capped = {Provider.ollama, Provider.openai_compatible, Provider.openrouter}
        ceilings = {provider: resolve_max_tokens(provider) for provider in capped}
        assert all(ceiling is not None and ceiling > 0 for ceiling in ceilings.values())
        assert len(set(ceilings.values())) == 1, ceilings

    def test_first_party_cloud_routes_stay_uncapped(self) -> None:
        """A first-party API keeps the model's own ceiling. No runaway decode has
        been observed on these, and a cap here would truncate a long findings
        payload for setups that never had the problem."""
        capped = {Provider.ollama, Provider.openai_compatible, Provider.openrouter}
        for provider in Provider:
            if provider not in capped:
                assert resolve_max_tokens(provider) is None, provider

    def test_a_configured_ceiling_wins_everywhere(self) -> None:
        assert resolve_max_tokens(Provider.ollama, 512) == 512
        assert resolve_max_tokens(Provider.openai, 512) == 512

    def test_zero_means_explicitly_uncapped(self) -> None:
        """The escape hatch, spelled the way the rest of the config spells one
        (`max_review_seconds: 0`, `context_lines: 0`): a long review that would
        rather run for half an hour than be truncated can say so, and a default
        nobody can turn off would be the wrong kind of default."""
        assert resolve_max_tokens(Provider.ollama, 0) is None

    def test_build_provider_applies_the_default_ceiling(self) -> None:
        provider = build_provider(Provider.ollama, "qwen3.5:4b")
        assert provider.default_opts.get("max_tokens") == resolve_max_tokens(Provider.ollama)

    def test_build_provider_sends_no_ceiling_for_cloud(self) -> None:
        provider = build_provider(Provider.openai, "gpt-4o", api_key="sk-test")
        assert "max_tokens" not in provider.default_opts

    def test_build_provider_honours_an_explicit_ceiling(self) -> None:
        provider = build_provider(Provider.ollama, "qwen3.5:4b", max_tokens=512)
        assert provider.default_opts.get("max_tokens") == 512

    def test_build_provider_sends_nothing_when_uncapped(self) -> None:
        """`max_tokens=0` must not reach litellm — it would ask the model for an
        empty answer rather than an unbounded one."""
        provider = build_provider(Provider.ollama, "qwen3.5:4b", max_tokens=0)
        assert "max_tokens" not in provider.default_opts


def test_build_provider_tells_the_adapter_whether_the_route_takes_an_effort() -> None:
    """The capability answer lives in the factory — it holds the Provider enum and
    the raw model name that `dropped_params` needs, and the adapter holds neither.

    Fails OPEN: only a map that POSITIVELY omits the param marks a route
    incapable, because litellm's map does not know the newest models."""
    from lgtmaybe.providers import factory as factory_mod

    seen: dict[str, object] = {}

    def fake_dropped(_provider: object, _model: str, params: object) -> list[str]:
        seen["params"] = list(params)  # type: ignore[arg-type]
        return ["reasoning_effort"]

    original = factory_mod.dropped_params
    factory_mod.dropped_params = fake_dropped  # type: ignore[assignment]
    try:
        provider = build_provider(Provider.openai, "gpt-5.5", api_key="k")
    finally:
        factory_mod.dropped_params = original  # type: ignore[assignment]

    assert "reasoning_effort" in seen["params"]  # type: ignore[operator]
    assert provider._effort_override_supported is False


def test_build_provider_defaults_to_capable_when_the_map_is_silent() -> None:
    provider = build_provider(Provider.openai, "gpt-5.5", api_key="k")

    assert provider._effort_override_supported is True


class TestDeclaredEffortCapability:
    """Which efforts litellm's map says a model takes, and its default — read
    once in the factory, which holds the route prefix the map keys on."""

    def test_flags_resolve_to_the_accepted_set(self) -> None:
        levels, default = declared_effort_capability(Provider.openai, "gpt-5.5")

        assert levels is not None
        assert "minimal" not in levels
        assert {"none", "low", "medium", "high"} <= levels
        assert default is None

    def test_the_default_is_read_alongside(self) -> None:
        levels, default = declared_effort_capability(Provider.openai, "gpt-5.1")

        assert default == "none"
        assert levels is not None and "none" in levels

    def test_a_prefixed_entry_is_found_under_its_route(self) -> None:
        levels, _ = declared_effort_capability(Provider.azure, "gpt-5")

        assert levels is not None and "none" not in levels

    def test_an_unknown_model_declares_nothing(self) -> None:
        assert declared_effort_capability(Provider.openai, "no-such-model-xyz") == (None, None)

    def test_a_mapped_model_with_no_effort_metadata_declares_nothing(self) -> None:
        """Most of the map omits `supports_reasoning` rather than setting it
        false; reading that silence as "accepts nothing" would take the lever
        away from exactly the models the map has not caught up with."""
        assert declared_effort_capability(Provider.openai, "gpt-4o") == (None, None)

    def test_a_resolver_failure_declares_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from litellm.router_utils import reasoning_effort_capability as capability

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("internal API moved")

        monkeypatch.setattr(capability, "resolve_supported_reasoning_efforts", boom)
        monkeypatch.setattr(capability, "declared_reasoning_efforts", boom)

        assert declared_effort_capability(Provider.openai, "gpt-5.5") == (None, None)

    def test_a_missing_resolver_module_declares_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        monkeypatch.setitem(sys.modules, "litellm.router_utils.reasoning_effort_capability", None)

        assert declared_effort_capability(Provider.openai, "gpt-5.5") == (None, None)


class TestBuildProviderStepsWithinTheDeclaredSet:
    def test_a_model_without_minimal_steps_from_low_to_off(self) -> None:
        provider = build_provider(Provider.openai, "gpt-5.5", api_key="k", reasoning_effort="low")

        assert provider.lower_reasoning_effort() == {"reasoning_effort": "none"}

    def test_a_model_without_off_gets_no_switch(self) -> None:
        provider = build_provider(
            Provider.azure, "gpt-5", api_key="k", api_base="https://x", reasoning_effort="low"
        )

        assert provider.disable_reasoning() is None

    def test_a_default_already_at_the_bottom_steps_nowhere(self) -> None:
        provider = build_provider(Provider.openai, "gpt-5.1", api_key="k")

        assert provider.lower_reasoning_effort() is None

    def test_an_unknown_model_keeps_todays_ladder(self) -> None:
        provider = build_provider(
            Provider.openai, "no-such-model-xyz", api_key="k", reasoning_effort="low"
        )

        assert provider.lower_reasoning_effort() == {"reasoning_effort": "minimal"}
        assert provider.disable_reasoning() == {"reasoning_effort": "none"}

    def test_a_resolver_failure_keeps_todays_ladder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from litellm.router_utils import reasoning_effort_capability as capability

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("internal API moved")

        monkeypatch.setattr(capability, "resolve_supported_reasoning_efforts", boom)
        monkeypatch.setattr(capability, "declared_reasoning_efforts", boom)
        provider = build_provider(Provider.openai, "gpt-5.5", api_key="k", reasoning_effort="low")

        assert provider.lower_reasoning_effort() == {"reasoning_effort": "minimal"}

    def test_healthy_request_opts_are_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The capability set steers the retry only: the opts every healthy call
        is sent with are byte-identical whether the map declared it or not."""
        from litellm.router_utils import reasoning_effort_capability as capability

        declared = build_provider(Provider.openai, "gpt-5.5", api_key="k", reasoning_effort="low")

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("internal API moved")

        monkeypatch.setattr(capability, "declared_reasoning_efforts", boom)
        silent = build_provider(Provider.openai, "gpt-5.5", api_key="k", reasoning_effort="low")

        assert declared._effort_levels is not None
        assert silent._effort_levels is None
        assert declared.default_opts == silent.default_opts


class TestClaudeThinkingSampling:
    """A reasoning effort on a Claude route turns into extended thinking, and
    Anthropic rejects a temperature beside it ("`temperature` may only be set
    to 1 when thinking is enabled"). litellm forwards both regardless, so the
    factory withholds the one the user did not ask for: the effort was
    configured, the temperature is our determinism default.
    """

    def test_anthropic_effort_withholds_temperature(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="lgtmaybe.providers.factory"):
            built = build_provider(
                Provider.anthropic,
                "claude-sonnet-4-5",
                api_key="k",
                reasoning_effort="medium",
                temperature=0.0,
            )
        assert "temperature" not in built.default_opts
        assert built.default_opts["reasoning_effort"] == "medium"
        assert any("temperature" in record.getMessage() for record in caplog.records)

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            (Provider.anthropic, "claude-sonnet-4-5"),
            (Provider.bedrock, "anthropic.claude-sonnet-4-5-20250929-v1:0"),
            (Provider.vertex, "claude-sonnet-4-5"),
        ],
    )
    def test_a_withheld_temperature_is_disclosed_as_dropped(
        self, provider: Provider, model: str
    ) -> None:
        """The withhold used to be an INFO log only: `temperature_dropped()`
        stayed False, so the engine's "temperature not sent" summary segment
        never appeared — while the same loss on Bedrock (the model rejecting the
        param, then a re-send without it) was disclosed. The factory records the
        drop through the adapter's own mechanism, so both losses read one
        channel. Every Claude route (native, Bedrock, Vertex) seeds the record."""
        built = build_provider(
            provider,
            model,
            api_key="k",
            reasoning_effort="medium",
            temperature=0.0,
        )
        assert built.temperature_dropped()

    def test_effort_none_keeps_temperature(self) -> None:
        """`none` disables thinking, and a non-thinking request takes a temperature."""
        built = build_provider(
            Provider.anthropic,
            "claude-sonnet-4-5",
            api_key="k",
            reasoning_effort="none",
            temperature=0.0,
        )
        assert built.default_opts["temperature"] == 0.0

    def test_no_effort_keeps_temperature(self) -> None:
        built = build_provider(
            Provider.anthropic, "claude-sonnet-4-5", api_key="k", temperature=0.0
        )
        assert built.default_opts["temperature"] == 0.0

    def test_a_kept_temperature_is_not_marked_dropped(self) -> None:
        built = build_provider(
            Provider.anthropic, "claude-sonnet-4-5", api_key="k", temperature=0.0
        )
        assert not built.temperature_dropped()

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            (Provider.bedrock, "anthropic.claude-sonnet-4-5-20250929-v1:0"),
            (Provider.bedrock, "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            (Provider.vertex, "claude-sonnet-4-5@20250929"),
        ],
    )
    def test_claude_on_a_cloud_route_is_treated_the_same(
        self, provider: Provider, model: str
    ) -> None:
        built = build_provider(provider, model, reasoning_effort="low", temperature=0.0)
        assert "temperature" not in built.default_opts

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            (Provider.openai, "gpt-5"),
            (Provider.vertex, "gemini-2.5-pro"),
            (Provider.bedrock, "amazon.nova-pro-v1:0"),
            (Provider.ollama, "qwen3:8b"),
        ],
    )
    def test_other_reasoning_routes_keep_temperature(self, provider: Provider, model: str) -> None:
        built = build_provider(
            provider, model, api_key="k", reasoning_effort="low", temperature=0.0
        )
        assert built.default_opts["temperature"] == 0.0


class TestInputBudget:
    """The prompt budget a model can actually take, read off the route.

    The engine's batching budget (`max_input_tokens`) defaults to a flat number
    that knows nothing about the model. litellm knows the context window of
    every hosted model in its map, and ollama's window is the `num_ctx` the
    factory itself sends — so the adapter can say how much prompt fits, and
    the engine can fit its batches to it instead of learning from a 400.
    """

    def test_ollama_budget_is_num_ctx_less_the_output_ceiling(self) -> None:
        built = build_provider(Provider.ollama, "qwen3:8b")
        assert (
            built.input_budget() == built.default_opts["num_ctx"] - built.default_opts["max_tokens"]
        )

    def test_ollama_budget_follows_an_overridden_num_ctx(self) -> None:
        built = build_provider(Provider.ollama, "qwen3:8b", num_ctx=8192, max_tokens=1024)
        assert built.input_budget() == 8192 - 1024

    @pytest.mark.parametrize("num_ctx", [8192, 16384])
    def test_a_window_the_output_ceiling_fills_still_gets_a_budget(self, num_ctx: int) -> None:
        """The default 16,384-token ceiling leaves nothing of an 8k or 16k
        window. Answering None there reads as "no opinion", and the engine keeps
        its 100k default — 100k-token batches into an 8k window. A small window
        is the case the fit exists for, so it still gets a positive cap.

        The provider logger does not propagate to root (caplog only saw it when
        an earlier test had touched it), so a handler is attached directly.
        """
        from lgtmaybe.providers import litellm_provider

        built = build_provider(Provider.ollama, "qwen3:8b", num_ctx=num_ctx)
        assert built.default_opts["max_tokens"] >= num_ctx

        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        litellm_provider._log.addHandler(handler)
        try:
            budget = built.input_budget()
        finally:
            litellm_provider._log.removeHandler(handler)

        assert budget is not None
        assert 0 < budget <= num_ctx
        warning = next(r for r in records if "max_tokens" in r.getMessage())
        # The numbers ride as fields JsonFormatter writes onto the line.
        assert warning.max_tokens == built.default_opts["max_tokens"]
        assert warning.window == num_ctx

    def test_hosted_budget_comes_from_the_model_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        monkeypatch.setattr(litellm, "get_model_info", lambda model: {"max_input_tokens": 65536})
        built = build_provider(Provider.openrouter, "deepseek/deepseek-chat", api_key="k")
        # openrouter carries the default finite ceiling, which the prompt must leave room for.
        assert built.input_budget() == 65536 - built.default_opts["max_tokens"]

    def test_an_uncapped_route_gets_the_whole_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        monkeypatch.setattr(litellm, "get_model_info", lambda model: {"max_input_tokens": 200000})
        built = build_provider(Provider.anthropic, "claude-sonnet-4-5", api_key="k")
        assert built.input_budget() == 200000

    def test_an_unmapped_model_has_no_opinion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import litellm

        def unmapped(model: str) -> dict:
            raise ValueError("This model isn't mapped yet")

        monkeypatch.setattr(litellm, "get_model_info", unmapped)
        built = build_provider(Provider.openai_compatible, "my-local", api_base="http://x")
        assert built.input_budget() is None

    def test_a_map_entry_without_a_window_has_no_opinion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import litellm

        monkeypatch.setattr(litellm, "get_model_info", lambda model: {"max_input_tokens": None})
        built = build_provider(Provider.openai, "gpt-5", api_key="k")
        assert built.input_budget() is None


def _models_called(provider: Any, *models: str) -> tuple[list[str], list[str | None]]:
    """The model litellm received for each ``complete(model=...)``, and the
    model each ``ProviderResult`` reports."""
    seen: list[str] = []

    def side_effect(*_args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["model"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=10),
        )

    with patch("litellm.completion", side_effect=side_effect):
        reported = [
            provider.complete([{"role": "user", "content": "a"}], model=m).model for m in models
        ]
    return seen, reported


class TestCallerNamedModelSlots:
    """One factory-built provider serves the triage, review and reflect slots,
    and each caller names its slot's model on ``complete``. The factory pins the
    resolved primary string, so a named model that differs from the primary has
    to be routed the same way and actually called — swapped back for the
    primary, ``reflect_model`` and ``triage_model`` were silently inert."""

    def test_a_reflect_or_triage_model_reaches_litellm_on_the_primary_s_route(self) -> None:
        provider = build_provider(Provider.anthropic, "claude-opus-4-1", api_key="sk-test")

        seen, reported = _models_called(provider, "claude-haiku-4-5", "claude-sonnet-4-5")

        assert seen == ["anthropic/claude-haiku-4-5", "anthropic/claude-sonnet-4-5"]
        assert reported == seen, "the result must name the model actually called"

    def test_the_primary_or_no_model_keeps_the_resolved_primary(self) -> None:
        provider = build_provider(Provider.ollama, "qwen3:27b")

        seen, reported = _models_called(provider, "qwen3:27b", "")

        assert seen == ["ollama_chat/qwen3:27b", "ollama_chat/qwen3:27b"]
        assert reported == seen


class TestCredentialsNeverReachALogLine:
    """``core/logging`` redacts registered values; the factory is where keys arrive."""

    @staticmethod
    def _logged(text: str) -> str:
        from lgtmaybe.core.logging import JsonFormatter

        return JsonFormatter().format(logging.makeLogRecord({"msg": text}))

    def test_the_api_key_is_redacted(self) -> None:
        build_provider(Provider.openai, "gpt-4o", api_key="sk-live-0123456789abcdef")

        assert "sk-live-0123456789abcdef" not in self._logged("sent sk-live-0123456789abcdef")

    def test_each_refreshed_azure_ad_token_is_redacted(self) -> None:
        tokens = iter(["eyJfirst", "eyJsecond"])
        built = build_provider(
            Provider.azure,
            "gpt-4o",
            api_base="https://x.openai.azure.com",
            azure_ad_token_provider=lambda: next(tokens),
        )
        fetch = built.default_opts["azure_ad_token_provider"]

        assert (fetch(), fetch()) == ("eyJfirst", "eyJsecond")
        logged = self._logged("bearer eyJfirst then eyJsecond")
        assert "eyJfirst" not in logged
        assert "eyJsecond" not in logged

    def test_the_keyless_placeholder_is_not_a_secret(self) -> None:
        from lgtmaybe.providers.factory import OPENAI_COMPATIBLE_PLACEHOLDER_KEY as placeholder

        build_provider(Provider.openai_compatible, "m", api_base="http://x", api_key=placeholder)

        assert placeholder in self._logged(f"key {placeholder}")
