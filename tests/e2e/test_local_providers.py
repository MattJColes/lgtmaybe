"""Live e2e: drive the real ``lgtmaybe`` CLI against a local model server.

This proves *compatibility* — that the app round-trips end-to-end through each
local serving stack (ollama, llama.cpp, vLLM) — not recall (that is
``evals/run.py``). It exercises the whole path a developer hits: arg parsing,
``.lgtmaybe.yml`` loading, the local git-diff context, the provider/credential
resolution, the litellm wire format, structured-output parsing, and rendering.

The three backends split across exactly two adapters:

  - ollama            → litellm's native ``ollama/`` route (api_base + ``num_ctx``)
  - llama.cpp / vLLM  → the ``openai-compatible`` route (``openai/`` + custom base)

so running all three covers both code paths plus each server's quirks (vLLM is
strict about the model id and JSON schema; llama.cpp is lax; ollama strips
qwen-style ``think`` blocks).

These need a live server and a downloaded model, so they are marked ``e2e`` and
DESELECTED from the default gate (``addopts = -m "not e2e"`` in pyproject). Run
them deliberately::

    scripts/e2e-up.sh            # stand up the servers + pull the tiny models
    uv run pytest -m e2e         # run every backend that is reachable

Each backend auto-skips when its server is not up, so starting only one (say
ollama) runs just that leg. Endpoints and model tags are overridable via env
(see ``Backend``) so the same test fits whatever you have running locally.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

# A small module with blatant, security-lens-visible bugs: a hardcoded GitHub
# token and a shell-injection sink. Even a 0.5B model should flag at least one,
# which keeps the "got a finding" assertion meaningful without chasing recall.
_BUGGY_MODULE = '''\
"""Report helpers."""
import subprocess

API_TOKEN = "ghp_aB3xK9mP2qR7sT1vW4yZ6cD8eF0gH2jK4lM"


def run_report(report_name):
    cmd = "generate-report --name " + report_name
    subprocess.run(cmd, shell=True)


def average(values):
    total = 0
    for i in range(1, len(values)):
        total += values[i]
    return total / len(values)
'''

# One lens, not the full nine-way fan-out — a tiny local model is slow, and the
# planted bugs are squarely security findings, so a single lens keeps a leg to
# one model call while still proving the round-trip.
_REPO_CONFIG = "categories:\n  - security\n"

# Per-request model timeout (seconds). Generous: a 0.5B model on CPU is slow.
_TIMEOUT = os.environ.get("LGTMAYBE_E2E_TIMEOUT", "600")


@dataclass(frozen=True)
class Backend:
    """One local serving stack, with env-overridable endpoint + model tag."""

    id: str
    provider: str
    base_env: str
    base_default: str
    model_env: str
    model_default: str
    health_path: str  # appended to the base to probe liveness

    @property
    def base(self) -> str:
        return os.environ.get(self.base_env, self.base_default)

    @property
    def model(self) -> str:
        return os.environ.get(self.model_env, self.model_default)


# Defaults match scripts/e2e-up.sh. The openai-compatible bases end in /v1, so
# the OpenAI-style /models probe lands on <base>/models.
BACKENDS = [
    Backend(
        id="ollama",
        provider="ollama",
        base_env="LGTMAYBE_E2E_OLLAMA_BASE",
        base_default="http://localhost:11434",
        model_env="LGTMAYBE_E2E_OLLAMA_MODEL",
        model_default="qwen3:0.6b",
        health_path="/api/tags",
    ),
    Backend(
        id="llamacpp",
        provider="openai-compatible",
        base_env="LGTMAYBE_E2E_LLAMACPP_BASE",
        base_default="http://localhost:8080/v1",
        model_env="LGTMAYBE_E2E_LLAMACPP_MODEL",
        model_default="qwen2.5-0.5b-instruct",
        health_path="/models",
    ),
    Backend(
        id="vllm",
        provider="openai-compatible",
        base_env="LGTMAYBE_E2E_VLLM_BASE",
        base_default="http://localhost:8000/v1",
        model_env="LGTMAYBE_E2E_VLLM_MODEL",
        model_default="Qwen/Qwen2.5-0.5B-Instruct",
        health_path="/models",
    ),
]


def _server_up(backend: Backend) -> bool:
    """True when the backend answers its health endpoint (any non-5xx)."""
    url = backend.base.rstrip("/") + backend.health_path
    try:
        return httpx.get(url, timeout=2.0).status_code < 500
    except httpx.HTTPError:
        return False


def _extract_findings(stdout: str) -> list[dict[str, object]]:
    """Pull the findings JSON array out of CLI stdout.

    The ``review`` command echoes the rendered JSON; any stray logging is
    tolerated by scanning lines bottom-up for the first that parses as a list.
    """
    for line in reversed([ln for ln in stdout.splitlines() if ln.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
    raise AssertionError(f"no JSON findings array found in CLI stdout:\n{stdout}")


@pytest.fixture
def review_repo(tmp_path: Path) -> tuple[Path, str]:
    """A temp git repo: clean baseline commit, then a commit adding the buggy file.

    Returns (repo_path, base_sha). The config lives in the BASELINE commit so it
    is not itself part of the reviewed diff — only ``payments.py`` is.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "e2e@example.com")
    git("config", "user.name", "lgtmaybe-e2e")
    git("config", "commit.gpgsign", "false")

    (repo / "README.md").write_text("# demo\n")
    (repo / ".lgtmaybe.yml").write_text(_REPO_CONFIG)
    git("add", "README.md", ".lgtmaybe.yml")
    git("commit", "-q", "-m", "chore: baseline")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "payments.py").write_text(_BUGGY_MODULE)
    git("add", "payments.py")
    git("commit", "-q", "-m", "feat: add report helper")
    return repo, base


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.id for b in BACKENDS])
def test_cli_review_against_local_provider(
    backend: Backend, review_repo: tuple[Path, str]
) -> None:
    """`lgtmaybe review` round-trips through a live local model and emits findings."""
    if not _server_up(backend):
        pytest.skip(f"{backend.id} server not reachable at {backend.base}")

    repo, base = review_repo
    cmd = [
        sys.executable,
        "-m",
        "lgtmaybe",
        "review",
        "--provider",
        backend.provider,
        "--model",
        backend.model,
        "--api-base",
        backend.base,
        "--base",
        base,
        "--no-reflect",  # a tiny model over-prunes its own findings
        "--format",
        "json",
        "--timeout",
        _TIMEOUT,
    ]
    result = subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=int(_TIMEOUT) + 120,
    )

    assert result.returncode == 0, (
        f"{backend.id}: CLI exited {result.returncode}\n"
        f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )

    findings = _extract_findings(result.stdout)
    # Every finding must carry the structured shape — proves schema enforcement
    # survived the live round-trip, not just that *some* JSON came back.
    required = {"path", "line", "severity", "title", "body"}
    for finding in findings:
        assert required <= set(finding), f"{backend.id}: malformed finding {finding}"

    # The file plants a hardcoded token + a shell-injection sink under the security
    # lens, so a working pipeline should surface at least one.
    assert findings, (
        f"{backend.id}: expected >=1 finding on a file with a hardcoded secret "
        f"and shell injection, got none"
    )
