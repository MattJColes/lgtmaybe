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
from lgtmaybe.providers.factory import (
    DEFAULT_OLLAMA_BASE,
    OPENAI_COMPATIBLE_PLACEHOLDER_KEY,
)

_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class AuthConfig:
    api_key: str | None = None
    api_base: str | None = None
    # Returns a fresh Azure AD (Entra) bearer token for keyless Azure OpenAI — set
    # instead of api_key when ambient cloud creds (GitHub OIDC / managed identity)
    # are used. A provider, not a token: litellm calls it per request, so a review
    # that outlives one token (~an hour) is not 401'd part-way through.
    azure_ad_token_provider: Callable[[], str] | None = None


# Data-plane scope for Azure OpenAI / Cognitive Services AD tokens.
_AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"

_AZURE_KEYLESS_GUIDANCE = (
    "azure requires credentials. Either set AZURE_API_KEY (or pass "
    "--api-key) for key-based auth, or configure keyless Azure AD "
    "credentials — GitHub OIDC via azure/login (needs id-token: write "
    "and a federated credential on your Entra app), or a local "
    "'az login' / managed identity."
)

# Env var carrying the key, for the plain API-key providers. Looked up (not
# .get) on purpose: a provider missing here is a bug, and a KeyError names it
# rather than resolving to a keyless AuthConfig that fails at the API.
_ENV_VAR: dict[Provider, str] = {
    Provider.openai: "OPENAI_API_KEY",
    Provider.anthropic: "ANTHROPIC_API_KEY",
    Provider.openrouter: "OPENROUTER_API_KEY",
}


def _default_gcp_probe() -> bool:
    """Detect ambient GCP credentials.

    Covers the CI/WIF env vars (``GOOGLE_APPLICATION_CREDENTIALS``) and a project
    set via any of the recognised vars (``GOOGLE_CLOUD_PROJECT``,
    ``GCLOUD_PROJECT``, ``CLOUDSDK_CORE_PROJECT``, ``VERTEXAI_PROJECT``), plus the
    Application Default Credentials well-known file written by
    ``gcloud auth application-default login`` — which sets no env var — so the
    documented local Vertex flow is recognised. Past that fast path it defers to
    ``google.auth.default()``, which alone can see metadata-server credentials
    (GCE, GKE Workload Identity, Cloud Run, Cloud Build) — the same reason
    Bedrock hands its chain to the AWS SDK.
    """

    if (
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
        or os.environ.get("CLOUDSDK_CORE_PROJECT")
        or os.environ.get("VERTEXAI_PROJECT")
    ):
        return True

    configured = os.environ.get("CLOUDSDK_CONFIG")
    if configured:
        config_dir = Path(configured)
    elif _WINDOWS:
        appdata = os.environ.get("APPDATA")
        config_dir = (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "gcloud"
    else:
        config_dir = Path.home() / ".config" / "gcloud"
    if (config_dir / "application_default_credentials.json").is_file():
        return True

    try:
        import google.auth  # the vertex extra

        google.auth.default()
    except Exception:  # ImportError, DefaultCredentialsError, a metadata timeout
        return False
    return True


def _default_azure_token_provider() -> Callable[[], str]:
    """Build a refreshing Azure AD token provider from ambient credentials.

    Uses azure-identity's ``DefaultAzureCredential``, which transparently covers
    GitHub OIDC workload-identity federation in CI (``AZURE_FEDERATED_TOKEN_FILE``
    + ``AZURE_CLIENT_ID`` + ``AZURE_TENANT_ID``) and local creds when developing
    (``az login``, a managed identity, or ``AZURE_*`` env vars). One token is
    fetched up front so a missing or misconfigured credential fails at start-up
    with the guidance, not at the first model call.
    """
    try:
        from azure.core.exceptions import ClientAuthenticationError
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as exc:
        raise ValueError(
            "keyless azure needs the azure-identity package. "
            "Install it with 'pip install lgtmaybe[azure]' (the GitHub Action "
            "image already bundles it)."
        ) from exc

    token_provider: Callable[[], str] = get_bearer_token_provider(
        DefaultAzureCredential(), _AZURE_OPENAI_SCOPE
    )
    try:
        token_provider()
    except ClientAuthenticationError as exc:
        # The chain's own detail (e.g. an AADSTS code) names what is misconfigured.
        raise ValueError(f"{_AZURE_KEYLESS_GUIDANCE}\n{exc}") from exc
    return token_provider


def resolve_credentials(
    provider: Provider,
    *,
    api_key: str | None = None,
    api_base: str | None = None,
    ambient_probe: Callable[[], bool] | None = None,
    azure_token_provider: Callable[[], str] | None = None,
) -> AuthConfig:
    """Resolve auth for the given provider.

    Raises ValueError with a clear remediation message when no valid auth
    can be found.
    """
    if provider is Provider.bedrock:
        # The AWS SDK owns this chain: unlike a local probe it can resolve ECS,
        # EKS Pod Identity and EC2 instance-profile credentials as well as env/files.
        return AuthConfig(api_base=api_base)

    if provider is Provider.vertex:
        probe = ambient_probe if ambient_probe is not None else _default_gcp_probe
        if not probe():
            raise ValueError(
                "vertex requires ambient GCP credentials. "
                "Configure Workload Identity Federation, set GOOGLE_APPLICATION_CREDENTIALS "
                "to a service-account key file, or run 'gcloud auth application-default login'."
            )
        return AuthConfig(api_base=api_base)

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
        token_provider = (
            azure_token_provider
            if azure_token_provider is not None
            else _default_azure_token_provider()
        )
        return AuthConfig(api_base=base, azure_ad_token_provider=token_provider)

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
    env_var = _ENV_VAR[provider]

    # An explicit --api-base (e.g. an OpenAI-format proxy) rides along with the
    # key — dropping it here would silently ignore the user's endpoint.
    if api_key:
        return AuthConfig(api_key=api_key, api_base=api_base)

    key_from_env = os.environ.get(env_var)
    if key_from_env:
        return AuthConfig(api_key=key_from_env, api_base=api_base)

    raise ValueError(
        f"{provider} requires an API key. Set the {env_var} environment variable or pass --api-key."
    )
