"""Shared test fixtures.

Provider-credential env vars leak in from the developer's shell or the CI runner
and make the credential resolver non-deterministic: a real ``OPENAI_API_KEY`` in
the environment would turn a "missing key must raise" test green by accident, and
a stray ``AWS_*`` var would make a "bedrock needs ambient creds" test pass when it
should fail. Clear them by default; a test that needs one sets it explicitly via
``monkeypatch.setenv``.
"""

from __future__ import annotations

import pytest

# Every env var the credential resolver / CLI probes consult to pick auth.
_PROVIDER_CRED_ENV = (
    # API-key providers
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "LGTMAYBE_API_KEY",
    # GLM / Zhipu AI (zai)
    "ZAI_API_KEY",
    "ZAI_API_BASE",
    # Ambient AWS creds (bedrock)
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    # Ambient GCP creds (vertex)
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    # Azure (hybrid): endpoint + key, or keyless ambient Azure AD creds
    "AZURE_API_KEY",
    "AZURE_API_BASE",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_FEDERATED_TOKEN_FILE",
)


@pytest.fixture(autouse=True)
def _isolate_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a clean, credential-free environment."""
    for var in _PROVIDER_CRED_ENV:
        monkeypatch.delenv(var, raising=False)
