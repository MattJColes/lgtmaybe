"""Credential resolver — chain of responsibility pattern.

Given a Provider and optional explicit credentials, resolves the auth config
or raises a clear, actionable error naming the exact remediation.

The ambient_probe parameter makes the cloud-creds check injectable so tests
never touch real AWS/GCP.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.constants import (
    DEFAULT_OLLAMA_BASE,
    OPENAI_COMPATIBLE_PLACEHOLDER_KEY,
)


@dataclass(frozen=True)
class AuthConfig:
    api_key: str | None = None
    api_base: str | None = None
    # An Azure AD (Entra) bearer token for keyless Azure OpenAI — set instead of
    # api_key when ambient cloud creds (GitHub OIDC / managed identity) are used.
    azure_ad_token: str | None = None


# Data-plane scope for Azure OpenAI / Cognitive Services AD tokens.
_AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"


def _default_aws_probe() -> bool:
    """Detect ambient AWS credentials.

    Covers the CI/OIDC env vars and exported keys, plus a local shared-config
    file (`~/.aws/credentials` / `~/.aws/config`, or the path given by
    ``AWS_SHARED_CREDENTIALS_FILE`` / ``AWS_CONFIG_FILE``) so the documented
    "credentials via ~/.aws" local flow is recognised even without
    ``AWS_PROFILE`` exported.
    """

    if (
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("AWS_PROFILE")
        or os.environ.get("AWS_ROLE_ARN")
        or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
    ):
        return True

    aws_home = Path.home() / ".aws"
    candidates = (
        os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or str(aws_home / "credentials"),
        os.environ.get("AWS_CONFIG_FILE") or str(aws_home / "config"),
    )
    return any(Path(path).is_file() for path in candidates)


def _default_gcp_probe() -> bool:
    """Detect ambient GCP credentials.

    Covers the CI/WIF env vars (``GOOGLE_APPLICATION_CREDENTIALS``) and a project
    set via any of the recognised vars (``GOOGLE_CLOUD_PROJECT``,
    ``GCLOUD_PROJECT``, ``CLOUDSDK_CORE_PROJECT``, ``VERTEXAI_PROJECT``), plus the
    Application Default Credentials well-known file written by
    ``gcloud auth application-default login`` — which sets no env var — so the
    documented local Vertex flow is recognised.
    """

    if (
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
        or os.environ.get("CLOUDSDK_CORE_PROJECT")
        or os.environ.get("VERTEXAI_PROJECT")
    ):
        return True

    config_dir = os.environ.get("CLOUDSDK_CONFIG") or str(Path.home() / ".config" / "gcloud")
    return (Path(config_dir) / "application_default_credentials.json").is_file()


def _default_azure_token() -> str | None:
    """Fetch an Azure AD access token for Azure OpenAI from ambient credentials.

    Uses azure-identity's ``DefaultAzureCredential``, which transparently covers
    GitHub OIDC workload-identity federation in CI (``AZURE_FEDERATED_TOKEN_FILE``
    + ``AZURE_CLIENT_ID`` + ``AZURE_TENANT_ID``) and local creds when developing
    (``az login``, a managed identity, or ``AZURE_*`` env vars). Returns ``None``
    when no ambient credential is available so the resolver can fall through to a
    clear error.
    """
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise ValueError(
            "keyless azure needs the azure-identity package. "
            "Install it with 'pip install lgtmaybe[azure]' (the GitHub Action "
            "image already bundles it)."
        ) from exc

    try:
        credential = DefaultAzureCredential()
        token: str = credential.get_token(_AZURE_OPENAI_SCOPE).token
        return token
    except Exception:
        return None


def resolve_credentials(
    provider: Provider,
    *,
    api_key: str | None = None,
    api_base: str | None = None,
    ambient_probe: Callable[[], bool] | None = None,
    azure_token_provider: Callable[[], str | None] | None = None,
) -> AuthConfig:
    """Resolve auth for the given provider.

    Raises ValueError with a clear remediation message when no valid auth
    can be found.
    """
    if provider is Provider.bedrock:
        probe = ambient_probe if ambient_probe is not None else _default_aws_probe
        if not probe():
            raise ValueError(
                "bedrock requires ambient AWS credentials. "
                "Configure an OIDC role (AWS_ROLE_ARN + AWS_WEB_IDENTITY_TOKEN_FILE), "
                "a named profile (AWS_PROFILE), or set AWS_ACCESS_KEY_ID / "
                "AWS_SECRET_ACCESS_KEY in the environment."
            )
        return AuthConfig()

    if provider is Provider.vertex:
        probe = ambient_probe if ambient_probe is not None else _default_gcp_probe
        if not probe():
            raise ValueError(
                "vertex requires ambient GCP credentials. "
                "Configure Workload Identity Federation, set GOOGLE_APPLICATION_CREDENTIALS "
                "to a service-account key file, or run 'gcloud auth application-default login'."
            )
        return AuthConfig()

    if provider is Provider.azure:
        base = api_base or os.environ.get("AZURE_API_BASE")
        if not base:
            raise ValueError(
                "azure requires the resource endpoint. Set the AZURE_API_BASE "
                "environment variable (e.g. https://<resource>.openai.azure.com) "
                "or pass --api-base."
            )

        # Key-based auth wins when a key is supplied (explicit or env).
        key = api_key or os.environ.get("AZURE_API_KEY")
        if key:
            return AuthConfig(api_key=key, api_base=base)

        # Keyless: ambient Azure AD creds — GitHub OIDC federation in CI, or a
        # local 'az login' / managed identity. No static key is stored.
        get_token = (
            azure_token_provider if azure_token_provider is not None else _default_azure_token
        )
        token = get_token()
        if token:
            return AuthConfig(api_base=base, azure_ad_token=token)

        raise ValueError(
            "azure requires credentials. Either set AZURE_API_KEY (or pass "
            "--api-key) for key-based auth, or configure keyless Azure AD "
            "credentials — GitHub OIDC via azure/login (needs id-token: write "
            "and a federated credential on your Entra app), or a local "
            "'az login' / managed identity."
        )

    if provider is Provider.ollama:
        return AuthConfig(api_base=api_base or DEFAULT_OLLAMA_BASE)

    if provider is Provider.zai:
        key = api_key or os.environ.get("ZAI_API_KEY")
        if not key:
            raise ValueError(
                "zai (GLM / Zhipu AI) requires an API key. Set the ZAI_API_KEY "
                "environment variable or pass --api-key."
            )
        # Key-only by default (litellm's native zai/ route targets the international
        # endpoint). An explicit base overrides it for the China / coding-plan
        # endpoints (e.g. https://open.bigmodel.cn/api/paas/v4).
        return AuthConfig(api_key=key, api_base=api_base or os.environ.get("ZAI_API_BASE"))

    if provider is Provider.openai_compatible:
        base = api_base or os.environ.get("OPENAI_COMPATIBLE_API_BASE")
        if not base:
            raise ValueError(
                "openai-compatible requires a base URL. Pass --api-base "
                "(e.g. http://localhost:8000/v1 for llama.cpp / LM Studio / vLLM, "
                "or https://api.deepseek.com/v1) or set OPENAI_COMPATIBLE_API_BASE."
            )
        # Key is optional: hosted endpoints (DeepSeek) need one, local servers
        # don't. When absent we still send a placeholder — the OpenAI client
        # litellm uses rejects an empty key.
        key = (
            api_key
            or os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            or OPENAI_COMPATIBLE_PLACEHOLDER_KEY
        )
        return AuthConfig(api_key=key, api_base=base)

    # API-key providers: openai, anthropic, openrouter
    _ENV_VAR: dict[Provider, str] = {
        Provider.openai: "OPENAI_API_KEY",
        Provider.anthropic: "ANTHROPIC_API_KEY",
        Provider.openrouter: "OPENROUTER_API_KEY",
    }
    env_var = _ENV_VAR[provider]

    if api_key:
        return AuthConfig(api_key=api_key)

    key_from_env = os.environ.get(env_var)
    if key_from_env:
        return AuthConfig(api_key=key_from_env)

    raise ValueError(
        f"{provider} requires an API key. Set the {env_var} environment variable or pass --api-key."
    )
