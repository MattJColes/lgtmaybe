"""Shared test fixtures and helpers.

Provider-credential env vars leak in from the developer's shell or the CI runner
and make the credential resolver non-deterministic: a real ``OPENAI_API_KEY`` in
the environment would turn a "missing key must raise" test green by accident, and
a stray ``AWS_*`` var would make a "bedrock needs ambient creds" test pass when it
should fail. Clear them by default; a test that needs one sets it explicitly via
``monkeypatch.setenv``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lgtmaybe.core.models import Provider, ReviewConfig

_WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


def read_workflow(name: str) -> tuple[str, dict]:
    """A workflow file as (raw text, parsed YAML).

    Both halves matter: the parsed tree for job/step structure, the raw text for
    the shell bodies and `${{ }}` expressions YAML flattens into plain strings.
    """
    text = (_WORKFLOWS / name).read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def make_cfg(**overrides: object) -> ReviewConfig:
    """A ReviewConfig for tests that don't care which provider they name.

    ollama needs no credentials and ``reflect=False`` keeps the fake provider's
    call log to the lens fan-out, which is what most suites assert on. A plain
    function rather than a fixture so call sites stay a one-line import.
    """
    base: dict[str, object] = {"provider": Provider.ollama, "model": "m", "reflect": False}
    base.update(overrides)
    return ReviewConfig(**base)  # type: ignore[arg-type]


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


@pytest.fixture(autouse=True)
def _isolate_working_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests independent of files in the developer's checkout."""
    monkeypatch.chdir(tmp_path)
