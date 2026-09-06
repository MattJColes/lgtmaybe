"""Provider matrix: assert the factory + resolver behave for *every* Provider.

These tests are driven off ``list(Provider)`` and a single contract table, so
adding a seventh backend with no row here fails loudly instead of silently
skipping a variation. Three axes are covered per provider:

  * factory   — the litellm model string (and fallback) is namespaced correctly;
  * auth      — key providers need a key, cloud providers are keyless-with-ambient,
                ollama needs nothing;
  * quirks    — cloud providers inject no ``api_key``; ollama carries an
                ``api_base`` and is billed at zero cost.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import BaseModel

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.credentials import resolve_credentials
from lgtmaybe.providers.factory import build_provider, litellm_model_string

# --- the contract table: one row per provider -------------------------------

# litellm model-string prefix per provider.
EXPECTED_PREFIX: dict[Provider, str] = {
    Provider.openai: "openai/",
    Provider.openrouter: "openrouter/",
    Provider.anthropic: "anthropic/",
    Provider.bedrock: "bedrock/",
    Provider.vertex: "vertex_ai/",
    Provider.azure: "azure/",
    Provider.ollama: "ollama_chat/",
    # OpenAI-compatible servers ride the openai route with a custom api_base.
    Provider.openai_compatible: "openai/",
    # GLM / Zhipu AI rides litellm's native zai/ route.
    Provider.zai: "zai/",
}

# Providers that authenticate with an API key, and the env var that supplies it.
KEY_PROVIDERS: dict[Provider, str] = {
    Provider.openai: "OPENAI_API_KEY",
    Provider.anthropic: "ANTHROPIC_API_KEY",
    Provider.openrouter: "OPENROUTER_API_KEY",
    Provider.zai: "ZAI_API_KEY",
}
# Providers that authenticate with ambient cloud creds (keyless).
CLOUD_PROVIDERS = (Provider.bedrock, Provider.vertex)
# Providers that need no auth at all.
NO_AUTH_PROVIDERS = (Provider.ollama,)
# Hybrid: always needs an endpoint, then EITHER a key OR an ambient AD token.
HYBRID_PROVIDERS = (Provider.azure,)
# Custom endpoint: always needs an api_base; the key is optional (placeholder
# sent for keyless local servers).
ENDPOINT_PROVIDERS = (Provider.openai_compatible,)


def test_every_provider_is_classified_exactly_once() -> None:
    """Guard: each Provider is in the prefix table and exactly one auth class.

    A new provider can't be merged without deciding its model namespace and how
    it authenticates — this is what makes the matrix below truly exhaustive.
    """
    assert set(EXPECTED_PREFIX) == set(Provider)
    auth_classes = [
        set(KEY_PROVIDERS),
        set(CLOUD_PROVIDERS),
        set(NO_AUTH_PROVIDERS),
        set(HYBRID_PROVIDERS),
        set(ENDPOINT_PROVIDERS),
    ]
    union = set().union(*auth_classes)
    assert union == set(Provider)
    # disjoint: no provider classified twice
    assert sum(len(c) for c in auth_classes) == len(union)


@pytest.mark.parametrize("provider", list(Provider))
class TestFactoryMatrix:
    def test_model_string_has_expected_prefix(self, provider: Provider) -> None:
        expected = EXPECTED_PREFIX[provider] + "the-model"
        assert litellm_model_string(provider, "the-model") == expected

    def test_build_provider_uses_resolved_model_string(self, provider: Provider) -> None:
        built = build_provider(provider, "the-model", api_key="k")
        assert built.model == EXPECTED_PREFIX[provider] + "the-model"

    def test_build_provider_namespaces_fallback_the_same_way(self, provider: Provider) -> None:
        built = build_provider(provider, "primary", fallback_model="backup", api_key="k")
        assert built.fallback_model == EXPECTED_PREFIX[provider] + "backup"


@pytest.mark.parametrize("provider", list(KEY_PROVIDERS))
class TestKeyProviderCredentials:
    def test_explicit_key_resolves(self, provider: Provider) -> None:
        assert resolve_credentials(provider, api_key="sk-explicit").api_key == "sk-explicit"

    def test_env_key_resolves(self, provider: Provider, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(KEY_PROVIDERS[provider], "sk-from-env")
        assert resolve_credentials(provider).api_key == "sk-from-env"

    def test_missing_key_raises_naming_the_env_var(self, provider: Provider) -> None:
        # conftest clears every provider env var, so this is deterministic.
        with pytest.raises(ValueError, match=KEY_PROVIDERS[provider]):
            resolve_credentials(provider)


@pytest.mark.parametrize("provider", CLOUD_PROVIDERS)
class TestCloudProviderCredentials:
    def test_ambient_present_resolves_keyless(self, provider: Provider) -> None:
        assert resolve_credentials(provider, ambient_probe=lambda: True).api_key is None

    def test_ambient_absent_uses_provider_native_behavior(self, provider: Provider) -> None:
        if provider is Provider.bedrock:
            assert resolve_credentials(provider, ambient_probe=lambda: False).api_key is None
            return
        with pytest.raises(ValueError, match=provider.value):
            resolve_credentials(provider, ambient_probe=lambda: False)

    def test_built_provider_injects_no_api_key(self, provider: Provider) -> None:
        """Keyless cloud auth: the factory must not put an api_key in the opts."""
        built = build_provider(provider, "the-model")
        assert "api_key" not in built.default_opts


class TestNoAuthProvider:
    def test_ollama_resolves_without_creds(self) -> None:
        assert resolve_credentials(Provider.ollama).api_key is None

    def test_ollama_default_api_base_is_localhost(self) -> None:
        assert "localhost" in (resolve_credentials(Provider.ollama).api_base or "")

    def test_ollama_build_provider_sets_api_base(self) -> None:
        built = build_provider(Provider.ollama, "llama3")
        assert built.default_opts.get("api_base")


_AZURE_BASE = "https://my-resource.openai.azure.com"


@pytest.mark.parametrize("provider", HYBRID_PROVIDERS)
class TestHybridProviderCredentials:
    """Azure resolves in BOTH modes — the property that earns it its own class.

    It always needs an endpoint, then either a static key or an ambient Azure AD
    token; it fits neither the pure-key nor the pure-cloud bucket above.
    """

    def test_requires_an_endpoint(self, provider: Provider) -> None:
        # A key but no endpoint must still fail (conftest clears AZURE_API_BASE).
        with pytest.raises(ValueError, match=provider.value):
            resolve_credentials(provider, api_key="k")

    def test_key_mode_resolves_with_key_and_base(self, provider: Provider) -> None:
        cfg = resolve_credentials(provider, api_key="k", api_base=_AZURE_BASE)
        assert cfg.api_key == "k"
        assert cfg.api_base == _AZURE_BASE
        assert cfg.azure_ad_token is None

    def test_keyless_mode_resolves_with_ambient_ad_token(self, provider: Provider) -> None:
        cfg = resolve_credentials(
            provider, api_base=_AZURE_BASE, azure_token_provider=lambda: "ad-token"
        )
        assert cfg.api_key is None
        assert cfg.azure_ad_token == "ad-token"
        assert cfg.api_base == _AZURE_BASE

    def test_build_provider_threads_the_endpoint(self, provider: Provider) -> None:
        built = build_provider(provider, "gpt-4o", api_key="k", api_base=_AZURE_BASE)
        assert built.default_opts.get("api_base") == _AZURE_BASE


class TestEngineBehaviourMatrix:
    """Engine behaviour that keys off the provider, asserted for EVERY Provider.

    Same spirit as the auth matrix above: a new provider can't be merged
    without deciding its fan-out concurrency, and the preset/deadline
    behaviour must hold whatever the backend is.
    """

    # Auto max_concurrency is 6 for every provider — wide enough to overlap the
    # fan-out, narrow enough that one API key does not rate-limit itself. Local
    # backends are no longer special-cased: what bounds their throughput is the
    # server's own parallelism setting, not this pool's width.
    @pytest.mark.parametrize("provider", list(Provider))
    def test_auto_concurrency_default(self, provider: Provider) -> None:
        from lgtmaybe.core.models import ReviewConfig
        from lgtmaybe.engine.engine import _resolve_workers

        cfg = ReviewConfig(provider=provider, model="m")
        assert _resolve_workers(cfg, task_count=99) == 6

    @pytest.mark.parametrize("provider", list(Provider))
    def test_fast_preset_makes_four_calls_on_every_provider(self, provider: Provider) -> None:
        """The fast lens set is four distinct concerns regardless of provider —
        auto-concurrency changes the schedule, never the call count."""
        from lgtmaybe.core.models import PRContext, ReviewConfig
        from lgtmaybe.engine import LLMReviewEngine
        from tests.fakes import FakeProvider

        ctx = PRContext(
            diff="@@ -1,1 +1,2 @@\n context\n+new line\n",
            changed_files=["a.py"],
            base_sha="a",
            head_sha="b",
            repo="o/r",
            pr_number=1,
        )
        cfg = ReviewConfig(provider=provider, model="m", reflect=False)
        fake = FakeProvider()
        LLMReviewEngine(fake).review(ctx, cfg)
        assert len(fake.calls) == 4

    @pytest.mark.parametrize("provider", list(Provider))
    def test_review_deadline_field_defaults_on_every_provider(self, provider: Provider) -> None:
        from lgtmaybe.core.models import ReviewConfig

        assert ReviewConfig(provider=provider, model="m").max_review_seconds == 3600


_CUSTOM_BASE = "https://api.deepseek.com/v1"


@pytest.mark.parametrize("provider", ENDPOINT_PROVIDERS)
class TestEndpointProviderCredentials:
    """openai-compatible: always needs an api_base; the key is optional.

    Hosted endpoints (DeepSeek) take a key; local servers (llama.cpp / LM Studio /
    vLLM) take none — and lgtmaybe supplies a placeholder so the OpenAI client,
    which rejects an empty key, still works.
    """

    def test_requires_an_endpoint(self, provider: Provider) -> None:
        with pytest.raises(ValueError, match=provider.value):
            resolve_credentials(provider, api_key="k")

    def test_explicit_key_and_base_resolve(self, provider: Provider) -> None:
        cfg = resolve_credentials(provider, api_key="sk-x", api_base=_CUSTOM_BASE)
        assert cfg.api_key == "sk-x"
        assert cfg.api_base == _CUSTOM_BASE

    def test_keyless_base_resolves_with_a_placeholder(self, provider: Provider) -> None:
        from lgtmaybe.providers.factory import OPENAI_COMPATIBLE_PLACEHOLDER_KEY

        cfg = resolve_credentials(provider, api_base=_CUSTOM_BASE)
        assert cfg.api_key == OPENAI_COMPATIBLE_PLACEHOLDER_KEY
        assert cfg.api_base == _CUSTOM_BASE

    def test_build_provider_threads_the_endpoint(self, provider: Provider) -> None:
        built = build_provider(provider, "deepseek-chat", api_key="k", api_base=_CUSTOM_BASE)
        assert built.default_opts.get("api_base") == _CUSTOM_BASE


# --- structured output survives litellm's translation, per provider ---------

# One representative model per provider: real ids, so litellm's capability map
# (and each route's own supported-params list) answers as it would in the field.
_SCHEMA_MODELS: dict[Provider, str] = {
    Provider.openai: "gpt-5",
    Provider.anthropic: "claude-sonnet-4-5",
    Provider.openrouter: "deepseek/deepseek-chat",
    Provider.bedrock: "anthropic.claude-sonnet-4-5-20250929-v1:0",
    Provider.vertex: "gemini-2.5-pro",
    Provider.azure: "gpt-4.1",
    Provider.ollama: "qwen3:8b",
    Provider.openai_compatible: "my-local-model",
    Provider.zai: "glm-4.6",
}

# The request-shape keys under which a route carries a structured-output
# contract after translation: OpenAI's own field, a forced tool (our recovery
# and litellm's own for anthropic), anthropic's native output_format, the
# bedrock Converse field, ollama's `format`, and Gemini's response_json_schema.
_SCHEMA_CARRIERS = (
    "response_format",
    "tools",
    "output_format",
    "outputConfig",
    "format",
    "response_json_schema",
)


class _Findings(BaseModel):
    findings: list[str]


@pytest.mark.parametrize("provider", list(Provider))
def test_the_schema_survives_litellm_translation(provider: Provider) -> None:
    """What the adapter sends is not what goes on the wire: litellm translates
    every request per route and, with `drop_params` on, silently removes any
    OpenAI-vocabulary param the route's list omits. Every other provider test
    patches `litellm.completion`, so that translation is never exercised — and
    a route that drops `response_format` (zai) reviewed with no schema at all
    while the adapter reported enforcement was on. This runs the real
    translation on the adapter's real kwargs, offline, for every provider.
    """
    import litellm

    built = build_provider(
        provider,
        _SCHEMA_MODELS[provider],
        api_key="k",
        api_base="https://example.invalid"
        if provider in (*HYBRID_PROVIDERS, *ENDPOINT_PROVIDERS)
        else None,
    )
    translated: list[dict] = []

    def translate(**kwargs):
        model, custom_llm_provider, _key, _base = litellm.get_llm_provider(kwargs["model"])
        request = {
            k: v
            for k, v in kwargs.items()
            if k in ("response_format", "tools", "tool_choice", "temperature", "max_tokens")
        }
        translated.append(
            litellm.get_optional_params(
                model=model, custom_llm_provider=custom_llm_provider, **request
            )
        )
        return _tool_reply() if "tools" in kwargs else _plain_reply()

    with patch("litellm.completion", side_effect=translate):
        built.complete(
            [{"role": "user", "content": "hi"}], _SCHEMA_MODELS[provider], response_format=_Findings
        )

    assert translated, "the adapter never reached litellm"
    on_the_wire = translated[-1]
    assert any(key in on_the_wire for key in _SCHEMA_CARRIERS), (
        f"{provider.value}: litellm's {built.model} translation carries no schema: "
        f"{sorted(on_the_wire)}"
    )


def _plain_reply():
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"findings": []}'), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


def _tool_reply():
    from types import SimpleNamespace

    call = SimpleNamespace(
        function=SimpleNamespace(name="lgtmaybe_structured_output", arguments='{"findings": []}')
    )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=[call]), finish_reason="tool_calls"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
