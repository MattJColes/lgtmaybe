"""Shared test fixtures and helpers.

Provider-credential env vars leak in from the developer's shell or the CI runner
and make the credential resolver non-deterministic: a real ``OPENAI_API_KEY`` in
the environment would turn a "missing key must raise" test green by accident, and
a stray ``AWS_*`` var would make a "bedrock needs ambient creds" test pass when it
should fail. Clear them by default; a test that needs one sets it explicitly via
``monkeypatch.setenv``.
"""

from __future__ import annotations

import os

# litellm resolves its model-capability map by DOWNLOADING
# model_prices_and_context_window.json at import time unless this is set, so
# `supports_prompt_caching(...)` answers from whatever upstream published today
# rather than from anything uv.lock pins. That turned a green commit red with no
# code change when two openrouter entries stopped declaring prompt caching. Pin
# the suite to the map shipped with the locked litellm, so the assertions are a
# function of the lockfile. `setdefault`, so a deliberate live-map run still
# works: `LITELLM_LOCAL_MODEL_COST_MAP=False uv run pytest`.
#
# This must precede every import below that can pull in litellm.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from pathlib import Path  # noqa: E402

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
    # ChatGPT subscription (chatgpt): litellm's token file and Codex endpoint
    "CHATGPT_TOKEN_DIR",
    "CHATGPT_AUTH_FILE",
    "CHATGPT_API_BASE",
    "OPENAI_CHATGPT_API_BASE",
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
def _isolate_provider_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Start every test from a clean, credential-free environment."""
    for var in _PROVIDER_CRED_ENV:
        monkeypatch.delenv(var, raising=False)
    # Unset, the ChatGPT sign-in is read from the developer's home directory, so
    # point it at an empty per-test directory instead.
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "chatgpt-tokens"))


@pytest.fixture(autouse=True)
def _isolate_registered_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test's fixture key (``"k"``) must not redact every later test's log line."""
    from lgtmaybe.core import logging as lgtmaybe_logging

    monkeypatch.setattr(lgtmaybe_logging, "_secrets", set(lgtmaybe_logging._secrets))


@pytest.fixture(autouse=True)
def _isolate_working_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests independent of files in the developer's checkout."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _isolate_installed_analyzers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep engine runs independent of which linters happen to be installed.

    Static analysis is on by default, so every review would otherwise spawn
    whatever ruff / mypy / semgrep sits on PATH and feed its hints into the
    prompt the test asserts on. The engine's runner returns nothing here; a test
    about static analysis patches ``lgtmaybe.engine.stages.run_static_analysis``
    itself, and ``tests/engine/test_static_analysis.py`` calls the real runner
    directly.
    """
    monkeypatch.setattr("lgtmaybe.engine.stages.run_static_analysis", lambda files, cfg: [])


@pytest.fixture(autouse=True)
def _isolate_user_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests independent of the developer's ~/.config/lgtmaybe/config.yml.

    There is no built-in provider, so a real user config would silently supply
    one and turn a "nothing configured" test green (or a configured one red).
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture(autouse=True)
def _authenticated_posting_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The HTTP fixture token belongs to the bot unless a test owns its identity boundary.

    GitHub's token is Actions' (``github-actions[bot]``); Gitea's and GitLab's
    ``/user`` lookups answer ``lgtmaybe-bot`` (GitLab: user id 99, unless the test
    mocks the route itself), so a fixture comment is "ours" only when it names
    that author.
    """
    import json

    import httpx

    original = httpx.Client.send

    def send(client, request, **kwargs):
        if (
            request.method == "POST"
            and str(request.url) == "https://api.github.com/graphql"
            and json.loads(request.content).get("query") == "query { viewer { login } }"
        ):
            return httpx.Response(
                200, json={"data": {"viewer": {"login": "github-actions[bot]"}}}, request=request
            )
        if request.method == "GET" and request.url.path == "/api/v1/user":
            return httpx.Response(200, json={"login": "lgtmaybe-bot"}, request=request)
        if request.method == "GET" and request.url.path == "/api/v4/user":
            # A test that mocks this route itself (respx) answers it; otherwise
            # the default identity does.
            try:
                return original(client, request, **kwargs)
            except Exception:  # noqa: BLE001 — unmocked or unreachable: use the default
                return httpx.Response(
                    200, json={"id": 99, "username": "lgtmaybe-bot"}, request=request
                )
        return original(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
