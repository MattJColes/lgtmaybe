"""Every provider, through the real CLI + real engine, down to litellm.

``lgtmaybe review`` is run once per provider with ``litellm.completion`` mocked
(the only fake). This proves the ``--provider``/``--model`` selection actually
reaches litellm as the right namespaced model string, and that each provider's
auth quirk is honoured end to end:

  * key providers (openai/anthropic/openrouter) send an ``api_key``;
  * cloud providers (bedrock/vertex) send NO ``api_key`` — keyless ambient creds;
  * ollama sends an ``api_base`` and is billed at zero cost.

This is the layer between the pure unit matrix and a real-spend action e2e: no
network, no real keys, but the live wiring (CLI → resolver → factory → engine →
litellm) is exercised.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import lgtmaybe.cli as cli_module
from lgtmaybe.cli import main
from lgtmaybe.core.models import PRContext

_CTX = PRContext(
    diff="@@ -1 +1 @@\n-a\n+b\n",
    changed_files=["src/app.py"],
    base_sha="base",
    head_sha="head",
    repo="org/repo",
    pr_number=0,
)


def _fake_response() -> SimpleNamespace:
    """A minimal litellm-shaped response carrying an empty (valid) findings array."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


@pytest.fixture
def captured_completion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    """Mock litellm at the boundary and feed the engine a local diff.

    Returns the list of kwargs every ``litellm.completion`` call received.

    Runs from an empty directory: the CLI probes ``.lgtmaybe.yml`` relative to
    the cwd, so a suite run from the repo root would otherwise assert against
    lgtmaybe's own dogfood config rather than against the defaults these tests
    are about — silently, until someone adds a key to it.
    """
    monkeypatch.chdir(tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return _fake_response()

    monkeypatch.setattr("litellm.completion", fake_completion)
    monkeypatch.setattr(cli_module, "local_pr_context", lambda **_: _CTX)
    return calls


# id, provider, model, extra CLI args, env to set, expected litellm model, expect api_key
CASES = [
    ("openai", "openai", "gpt-4o", ["--api-key", "sk-x"], {}, "openai/gpt-4o", True),
    ("anthropic", "anthropic", "claude-x", ["--api-key", "sk-x"], {}, "anthropic/claude-x", True),
    (
        "openrouter",
        "openrouter",
        "vendor/m",
        ["--api-key", "sk-x"],
        {},
        "openrouter/vendor/m",
        True,
    ),
    (
        "bedrock",
        "bedrock",
        "anthropic.claude-x",
        [],
        {"AWS_ACCESS_KEY_ID": "AKIA"},
        "bedrock/anthropic.claude-x",
        False,
    ),
    (
        "vertex",
        "vertex",
        "claude-x",
        [],
        {"GOOGLE_CLOUD_PROJECT": "proj"},
        "vertex_ai/claude-x",
        False,
    ),
    (
        "azure",
        "azure",
        "gpt-4o",
        ["--api-key", "sk-x", "--api-base", "https://r.openai.azure.com"],
        {},
        "azure/gpt-4o",
        True,
    ),
    ("ollama", "ollama", "llama3", [], {}, "ollama/llama3", False),
    (
        "openai-compatible",
        "openai-compatible",
        "deepseek-chat",
        ["--api-key", "sk-x", "--api-base", "https://api.deepseek.com/v1"],
        {},
        "openai/deepseek-chat",
        True,
    ),
]


@pytest.mark.parametrize(
    "name,provider,model,extra,env,expected_model,expect_api_key",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_review_threads_provider_to_litellm(
    name: str,
    provider: str,
    model: str,
    extra: list[str],
    env: dict[str, str],
    expected_model: str,
    expect_api_key: bool,
    captured_completion: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    result = CliRunner().invoke(
        main,
        ["review", "--provider", provider, "--model", model, "--no-reflect", *extra],
    )

    assert result.exit_code == 0, result.output
    assert captured_completion, "litellm.completion was never called"
    call = captured_completion[0]
    assert call["model"] == expected_model
    if expect_api_key:
        assert call.get("api_key") == "sk-x"
    else:
        # cloud + ollama never carry a static api_key
        assert "api_key" not in call


def test_ollama_call_carries_api_base(captured_completion: list[dict[str, Any]]) -> None:
    """ollama must reach litellm with an api_base (default localhost)."""
    result = CliRunner().invoke(
        main, ["review", "--provider", "ollama", "--model", "llama3", "--no-reflect"]
    )

    assert result.exit_code == 0, result.output
    assert captured_completion[0].get("api_base")


def test_openai_compatible_call_carries_custom_base(
    captured_completion: list[dict[str, Any]],
) -> None:
    """A custom OpenAI-compatible endpoint must reach litellm with its api_base."""
    result = CliRunner().invoke(
        main,
        [
            "review",
            "--provider",
            "openai-compatible",
            "--model",
            "deepseek-chat",
            "--api-base",
            "https://api.deepseek.com/v1",
            "--api-key",
            "sk-x",
            "--no-reflect",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_completion[0].get("api_base") == "https://api.deepseek.com/v1"


def test_openai_compatible_keyless_local_server_sends_base_and_placeholder(
    captured_completion: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local llama.cpp / LM Studio / vLLM server needs no key — the base reaches
    litellm and a placeholder key is supplied (the OpenAI client demands one)."""
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    result = CliRunner().invoke(
        main,
        [
            "review",
            "--provider",
            "openai-compatible",
            "--model",
            "local-model",
            "--api-base",
            "http://localhost:8000/v1",
            "--no-reflect",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_completion[0].get("api_base") == "http://localhost:8000/v1"
    assert captured_completion[0].get("api_key")


def test_structured_output_sends_response_format_by_default(
    captured_completion: list[dict[str, Any]],
) -> None:
    """By default the findings JSON schema reaches litellm as response_format."""
    result = CliRunner().invoke(
        main, ["review", "--provider", "ollama", "--model", "llama3", "--no-reflect"]
    )

    assert result.exit_code == 0, result.output
    assert captured_completion[0].get("response_format") is not None


def test_no_structured_output_omits_response_format(
    captured_completion: list[dict[str, Any]],
) -> None:
    """--no-structured-output is the escape hatch for a gateway that rejects
    response_format: litellm must then be called without it (issue #104)."""
    result = CliRunner().invoke(
        main,
        [
            "review",
            "--provider",
            "openai-compatible",
            "--model",
            "gemini-3.5-flash",
            "--api-base",
            "https://api.myllm.com/v1",
            "--api-key",
            "sk-x",
            "--no-reflect",
            "--no-structured-output",
        ],
    )

    assert result.exit_code == 0, result.output
    assert all("response_format" not in call for call in captured_completion)


def test_num_ctx_flag_reaches_litellm_for_ollama(
    captured_completion: list[dict[str, Any]],
) -> None:
    """--num-ctx raises ollama's context window so big diffs aren't truncated."""
    result = CliRunner().invoke(
        main,
        [
            "review",
            "--provider",
            "ollama",
            "--model",
            "llama3",
            "--no-reflect",
            "--num-ctx",
            "32768",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_completion[0].get("num_ctx") == 32768


def test_num_ctx_flag_is_ignored_for_hosted_provider(
    captured_completion: list[dict[str, Any]],
) -> None:
    """num_ctx is ollama-only — a hosted provider must never receive it (litellm rejects it)."""
    result = CliRunner().invoke(
        main,
        [
            "review",
            "--provider",
            "openai",
            "--model",
            "gpt-4o",
            "--api-key",
            "sk-x",
            "--no-reflect",
            "--num-ctx",
            "32768",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "num_ctx" not in captured_completion[0]


def test_max_tokens_flag_reaches_litellm(captured_completion: list[dict[str, Any]]) -> None:
    """`--max-tokens` caps each completion. Prepaid routes (OpenRouter) reserve
    prompt + max_tokens against the balance BEFORE generating, and fall back to the
    model's full output ceiling when the request omits it — so an uncapped review
    can be refused for credit it was never going to spend. The cap has to reach
    litellm to shrink that reservation."""
    result = CliRunner().invoke(
        main,
        [
            "review",
            "--provider",
            "openrouter",
            "--model",
            "vendor/m",
            "--api-key",
            "sk-x",
            "--no-reflect",
            "--max-tokens",
            "8192",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_completion[0].get("max_tokens") == 8192


def test_max_tokens_is_absent_by_default(captured_completion: list[dict[str, Any]]) -> None:
    """Unset means uncapped — the request must carry no max_tokens at all, so the
    model's own ceiling applies and nothing silently truncates a long findings
    payload."""
    result = CliRunner().invoke(
        main,
        [
            "review",
            "--provider",
            "openai",
            "--model",
            "gpt-4o",
            "--api-key",
            "sk-x",
            "--no-reflect",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "max_tokens" not in captured_completion[0]
