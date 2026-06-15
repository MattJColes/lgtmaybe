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

import pytest

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
    Provider.ollama: "ollama/",
    # OpenAI-compatible servers ride the openai route with a custom api_base.
    Provider.openai_compatible: "openai/",
}

# Providers that authenticate with an API key, and the env var that supplies it.
KEY_PROVIDERS: dict[Provider, str] = {
    Provider.openai: "OPENAI_API_KEY",
    Provider.anthropic: "ANTHROPIC_API_KEY",
    Provider.openrouter: "OPENROUTER_API_KEY",
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

    def test_ambient_absent_raises_actionable_error(self, provider: Provider) -> None:
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
        from lgtmaybe.providers.constants import OPENAI_COMPATIBLE_PLACEHOLDER_KEY

        cfg = resolve_credentials(provider, api_base=_CUSTOM_BASE)
        assert cfg.api_key == OPENAI_COMPATIBLE_PLACEHOLDER_KEY
        assert cfg.api_base == _CUSTOM_BASE

    def test_build_provider_threads_the_endpoint(self, provider: Provider) -> None:
        built = build_provider(provider, "deepseek-chat", api_key="k", api_base=_CUSTOM_BASE)
        assert built.default_opts.get("api_base") == _CUSTOM_BASE
