"""Tests for the credential resolver (chain of responsibility)."""

from __future__ import annotations

import pytest

import lgtmaybe.providers.credentials as credentials
from lgtmaybe.core.models import Provider
from lgtmaybe.providers.credentials import (
    _default_aws_probe,
    _default_gcp_probe,
    resolve_credentials,
)

# ---- probe stubs ----


def _ambient_present() -> bool:
    return True


def _ambient_absent() -> bool:
    return False


class TestBedrock:
    def test_bedrock_with_ambient_creds_resolves_keyless(self) -> None:
        config = resolve_credentials(
            Provider.bedrock,
            ambient_probe=_ambient_present,
        )
        assert config.api_key is None

    def test_bedrock_without_ambient_creds_raises_helpful_error(self) -> None:
        with pytest.raises(ValueError, match="bedrock") as exc_info:
            resolve_credentials(
                Provider.bedrock,
                ambient_probe=_ambient_absent,
            )
        # Error must name a concrete remediation
        assert (
            "AWS" in str(exc_info.value)
            or "OIDC" in str(exc_info.value)
            or "aws" in str(exc_info.value).lower()
        )

    def test_bedrock_error_message_names_the_provider(self) -> None:
        with pytest.raises(ValueError, match="bedrock"):
            resolve_credentials(Provider.bedrock, ambient_probe=_ambient_absent)

    def test_bedrock_threads_api_base_through(self) -> None:
        """A custom endpoint (e.g. a gateway) passes through untouched."""
        config = resolve_credentials(
            Provider.bedrock, ambient_probe=_ambient_present, api_base="http://proxy:4000"
        )
        assert config.api_base == "http://proxy:4000"


class TestVertex:
    def test_vertex_with_ambient_creds_resolves_keyless(self) -> None:
        config = resolve_credentials(
            Provider.vertex,
            ambient_probe=_ambient_present,
        )
        assert config.api_key is None

    def test_vertex_without_ambient_creds_raises_helpful_error(self) -> None:
        with pytest.raises(ValueError, match="vertex") as exc_info:
            resolve_credentials(
                Provider.vertex,
                ambient_probe=_ambient_absent,
            )
        assert (
            "GCP" in str(exc_info.value)
            or "GOOGLE" in str(exc_info.value)
            or "gcp" in str(exc_info.value).lower()
            or "google" in str(exc_info.value).lower()
        )

    def test_vertex_threads_api_base_through(self) -> None:
        config = resolve_credentials(
            Provider.vertex, ambient_probe=_ambient_present, api_base="http://proxy:4000"
        )
        assert config.api_base == "http://proxy:4000"


class TestOpenAI:
    def test_openai_with_api_key_resolves(self) -> None:
        config = resolve_credentials(Provider.openai, api_key="sk-abc")
        assert config.api_key == "sk-abc"

    def test_openai_without_key_raises_helpful_error(self) -> None:
        with pytest.raises(ValueError, match="openai") as exc_info:
            resolve_credentials(Provider.openai)
        msg = str(exc_info.value).lower()
        # Must tell user how to fix it
        assert "api" in msg or "key" in msg or "OPENAI_API_KEY" in str(exc_info.value)

    def test_openai_error_names_the_env_var(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            resolve_credentials(Provider.openai)
        assert "OPENAI_API_KEY" in str(exc_info.value)

    def test_openai_threads_api_base_through(self) -> None:
        """--api-base must reach the client (e.g. an OpenAI-format proxy)."""
        config = resolve_credentials(
            Provider.openai, api_key="sk-abc", api_base="http://proxy:4000/v1"
        )
        assert config.api_base == "http://proxy:4000/v1"


class TestAnthropic:
    def test_anthropic_with_api_key_resolves(self) -> None:
        config = resolve_credentials(Provider.anthropic, api_key="sk-ant-xyz")
        assert config.api_key == "sk-ant-xyz"

    def test_anthropic_without_key_raises_helpful_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            resolve_credentials(Provider.anthropic)
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)

    def test_anthropic_threads_api_base_through(self) -> None:
        config = resolve_credentials(
            Provider.anthropic, api_key="sk-ant-xyz", api_base="http://proxy:4000"
        )
        assert config.api_base == "http://proxy:4000"


class TestOpenRouter:
    def test_openrouter_with_api_key_resolves(self) -> None:
        config = resolve_credentials(Provider.openrouter, api_key="sk-or-test")
        assert config.api_key == "sk-or-test"

    def test_openrouter_without_key_raises_helpful_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            resolve_credentials(Provider.openrouter)
        assert "OPENROUTER_API_KEY" in str(exc_info.value)

    def test_openrouter_threads_api_base_through(self) -> None:
        config = resolve_credentials(
            Provider.openrouter, api_key="sk-or-test", api_base="http://proxy:4000/v1"
        )
        assert config.api_base == "http://proxy:4000/v1"


class TestZai:
    """GLM / Zhipu AI: a pure API-key provider with an optional endpoint override."""

    def test_zai_with_api_key_resolves(self) -> None:
        config = resolve_credentials(Provider.zai, api_key="zai-secret")
        assert config.api_key == "zai-secret"

    def test_zai_reads_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZAI_API_KEY", "zai-env")
        config = resolve_credentials(Provider.zai)
        assert config.api_key == "zai-env"

    def test_zai_without_key_raises_naming_the_env_var(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            resolve_credentials(Provider.zai)
        assert "ZAI_API_KEY" in str(exc_info.value)

    def test_zai_threads_optional_api_base_override(self) -> None:
        """The China / coding-plan endpoint flows through instead of being dropped."""
        config = resolve_credentials(
            Provider.zai,
            api_key="zai-secret",
            api_base="https://open.bigmodel.cn/api/paas/v4",
        )
        assert config.api_base == "https://open.bigmodel.cn/api/paas/v4"

    def test_zai_reads_api_base_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZAI_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
        config = resolve_credentials(Provider.zai, api_key="zai-secret")
        assert config.api_base == "https://open.bigmodel.cn/api/paas/v4"

    def test_zai_without_override_leaves_base_unset(self) -> None:
        """No override → litellm's native zai/ default endpoint is used."""
        config = resolve_credentials(Provider.zai, api_key="zai-secret")
        assert config.api_base is None


class TestAzure:
    def test_azure_with_api_key_and_base_resolves(self) -> None:
        config = resolve_credentials(
            Provider.azure,
            api_key="azure-secret",
            api_base="https://my-resource.openai.azure.com",
        )
        assert config.api_key == "azure-secret"
        assert config.api_base == "https://my-resource.openai.azure.com"
        assert config.azure_ad_token is None

    def test_azure_reads_key_and_base_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_API_KEY", "env-secret")
        monkeypatch.setenv("AZURE_API_BASE", "https://env-resource.openai.azure.com")
        config = resolve_credentials(Provider.azure)
        assert config.api_key == "env-secret"
        assert config.api_base == "https://env-resource.openai.azure.com"

    def test_azure_without_base_raises_helpful_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_API_BASE", raising=False)
        with pytest.raises(ValueError, match="azure") as exc_info:
            resolve_credentials(Provider.azure, api_key="azure-secret")
        assert "AZURE_API_BASE" in str(exc_info.value)

    def test_azure_keyless_resolves_with_ambient_ad_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key, but ambient Azure AD creds yield a token — the keyless path."""
        monkeypatch.delenv("AZURE_API_KEY", raising=False)
        config = resolve_credentials(
            Provider.azure,
            api_base="https://my-resource.openai.azure.com",
            azure_token_provider=lambda: "ad-token-xyz",
        )
        assert config.api_key is None
        assert config.azure_ad_token == "ad-token-xyz"
        assert config.api_base == "https://my-resource.openai.azure.com"

    def test_azure_key_mode_preferred_over_keyless(self) -> None:
        """When a key is present the AD token provider is never consulted."""

        def _must_not_run() -> str | None:
            raise AssertionError("token provider should not be called in key mode")

        config = resolve_credentials(
            Provider.azure,
            api_key="azure-secret",
            api_base="https://my-resource.openai.azure.com",
            azure_token_provider=_must_not_run,
        )
        assert config.api_key == "azure-secret"
        assert config.azure_ad_token is None

    def test_azure_no_key_and_no_ambient_creds_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AZURE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="azure") as exc_info:
            resolve_credentials(
                Provider.azure,
                api_base="https://my-resource.openai.azure.com",
                azure_token_provider=lambda: None,
            )
        msg = str(exc_info.value)
        assert "AZURE_API_KEY" in msg
        assert "OIDC" in msg or "keyless" in msg.lower()


class TestOpenAICompatible:
    """Custom OpenAI-compatible endpoints: DeepSeek, llama.cpp, LM Studio, vLLM."""

    def test_requires_a_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_BASE", raising=False)
        with pytest.raises(ValueError, match="openai-compatible") as exc_info:
            resolve_credentials(Provider.openai_compatible)
        assert "api-base" in str(exc_info.value) or "base URL" in str(exc_info.value)

    def test_with_api_key_and_base_resolves(self) -> None:
        config = resolve_credentials(
            Provider.openai_compatible,
            api_key="sk-deepseek",
            api_base="https://api.deepseek.com/v1",
        )
        assert config.api_key == "sk-deepseek"
        assert config.api_base == "https://api.deepseek.com/v1"

    def test_reads_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-env")
        config = resolve_credentials(
            Provider.openai_compatible,
            api_base="https://api.deepseek.com/v1",
        )
        assert config.api_key == "sk-env"

    def test_reads_base_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_BASE", "http://localhost:8000/v1")
        config = resolve_credentials(Provider.openai_compatible, api_key="sk-x")
        assert config.api_base == "http://localhost:8000/v1"

    def test_keyless_local_server_uses_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """llama.cpp / LM Studio / vLLM need no key — but the OpenAI client demands
        a non-empty one, so a harmless placeholder is sent and the base preserved."""
        from lgtmaybe.providers.factory import OPENAI_COMPATIBLE_PLACEHOLDER_KEY

        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        config = resolve_credentials(
            Provider.openai_compatible,
            api_base="http://localhost:8000/v1",
        )
        assert config.api_key == OPENAI_COMPATIBLE_PLACEHOLDER_KEY
        assert config.api_base == "http://localhost:8000/v1"


class TestOllama:
    def test_ollama_resolves_with_no_key_or_creds(self) -> None:
        config = resolve_credentials(Provider.ollama)
        assert config.api_key is None

    def test_ollama_resolves_with_custom_api_base(self) -> None:
        config = resolve_credentials(Provider.ollama, api_base="http://host.docker.internal:11434")
        assert config.api_base == "http://host.docker.internal:11434"

    def test_ollama_default_api_base_is_localhost(self) -> None:
        config = resolve_credentials(Provider.ollama)
        assert "localhost" in (config.api_base or "")


_GCP_ENV_VARS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "VERTEXAI_PROJECT",
    "CLOUDSDK_CORE_PROJECT",
    "CLOUDSDK_CONFIG",
)

_AWS_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
)


def _clear(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...], home: str) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)
    # Redirect both host conventions so real local cloud config can't leak in.
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)


class TestDefaultGcpProbe:
    """The real ambient-GCP probe must recognise the documented local Vertex setup."""

    def test_vertexai_project_alone_is_detected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _clear(monkeypatch, _GCP_ENV_VARS, str(tmp_path))
        monkeypatch.setenv("VERTEXAI_PROJECT", "my-project")
        assert _default_gcp_probe() is True

    def test_cloudsdk_core_project_is_detected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _clear(monkeypatch, _GCP_ENV_VARS, str(tmp_path))
        monkeypatch.setenv("CLOUDSDK_CORE_PROJECT", "my-project")
        assert _default_gcp_probe() is True

    def test_adc_well_known_file_is_detected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """`gcloud auth application-default login` writes this file and sets no env var."""
        _clear(monkeypatch, _GCP_ENV_VARS, str(tmp_path))
        gcloud_dir = tmp_path / "gcloud"
        gcloud_dir.mkdir()
        (gcloud_dir / "application_default_credentials.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_dir))
        assert _default_gcp_probe() is True

    def test_gcp_probe_finds_adc_under_appdata_on_windows(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _clear(monkeypatch, _GCP_ENV_VARS, str(tmp_path))
        monkeypatch.setattr(credentials, "_WINDOWS", True, raising=False)
        appdata = tmp_path / "AppData" / "Roaming"
        gcloud_dir = appdata / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))

        assert _default_gcp_probe() is True

    def test_gcp_probe_prefers_cloudsdk_config_over_appdata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _clear(monkeypatch, _GCP_ENV_VARS, str(tmp_path))
        monkeypatch.setattr(credentials, "_WINDOWS", True, raising=False)
        appdata = tmp_path / "AppData" / "Roaming"
        gcloud_dir = appdata / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "explicit-empty"))

        assert _default_gcp_probe() is False

    def test_no_creds_anywhere_is_absent(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        _clear(monkeypatch, _GCP_ENV_VARS, str(tmp_path))
        monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "empty"))
        assert _default_gcp_probe() is False

    def test_vertex_resolves_keyless_with_only_vertexai_project(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """End-to-end: the documented local flow no longer raises."""
        _clear(monkeypatch, _GCP_ENV_VARS, str(tmp_path))
        monkeypatch.setenv("VERTEXAI_PROJECT", "my-project")
        config = resolve_credentials(Provider.vertex)
        assert config.api_key is None


class TestDefaultAwsProbe:
    """The real ambient-AWS probe must recognise a shared-credentials file (`~/.aws`)."""

    def test_shared_credentials_file_is_detected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _clear(monkeypatch, _AWS_ENV_VARS, str(tmp_path))
        creds = tmp_path / "credentials"
        creds.write_text("[default]\naws_access_key_id = AKIA\n")
        monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
        assert _default_aws_probe() is True

    def test_no_creds_anywhere_is_absent(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        _clear(monkeypatch, _AWS_ENV_VARS, str(tmp_path))
        assert _default_aws_probe() is False
