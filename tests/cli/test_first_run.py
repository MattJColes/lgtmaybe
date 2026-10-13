"""First run with nothing configured: fail fast with a pointer, never guess.

There is no built-in provider/model (it used to be ollama/llama3, which failed
for most users minutes into a run). Every entrypoint that loads config must
stop before any git or model work and say how to configure one, phrased for
that entrypoint's own configuration surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import lgtmaybe.cli as cli_module
from lgtmaybe.cli import main


@pytest.fixture
def no_work(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record (and refuse) any git or provider work, which must never start."""
    started: list[str] = []

    def _refuse(name: str):
        def _record(*_args: object, **_kwargs: object) -> None:
            started.append(name)
            raise AssertionError(f"{name} ran before the provider was configured")

        return _record

    monkeypatch.setattr(cli_module, "local_pr_context", _refuse("local_pr_context"))
    monkeypatch.setattr(cli_module, "build_provider_engine", _refuse("build_provider_engine"))
    monkeypatch.setattr(cli_module, "build_review_context", _refuse("build_review_context"))
    return started


@pytest.mark.parametrize("command", ["review", "diagram"])
def test_local_command_without_provider_points_at_config_init(
    command: str, no_work: list[str]
) -> None:
    result = CliRunner().invoke(main, [command])

    assert result.exit_code != 0
    assert "No provider configured" in result.output
    assert "lgtmaybe config init" in result.output
    assert "--provider" in result.output
    assert "Traceback" not in result.output
    assert no_work == []


def test_review_with_provider_but_no_model_names_the_model(no_work: list[str]) -> None:
    result = CliRunner().invoke(main, ["review", "--provider", "ollama"])

    assert result.exit_code != 0
    assert "No model configured" in result.output
    assert no_work == []


def test_pointer_is_two_lines(no_work: list[str]) -> None:
    """The pointer is short enough to read at a glance: what, how, why."""
    result = CliRunner().invoke(main, ["review"])

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2, result.output


def test_user_config_written_by_init_configures_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """After `config init` (here: its file), the same bare command proceeds."""
    from lgtmaybe.config import store

    store.save({"provider": "ollama", "model": "qwen3.6:27b"})
    seen: dict[str, object] = {}

    def _capture(cfg, runtime, **_kwargs):
        seen["cfg"] = cfg
        raise RuntimeError("stop after config")

    monkeypatch.setattr(cli_module, "build_provider_engine", _capture)

    result = CliRunner().invoke(main, ["review"])

    assert "No provider configured" not in result.output
    assert seen["cfg"].provider == "ollama"  # type: ignore[attr-defined]
    assert seen["cfg"].model == "qwen3.6:27b"  # type: ignore[attr-defined]


def _event(tmp_path: Path) -> Path:
    path = tmp_path / "event.json"
    path.write_text(
        json.dumps({"repository": {"full_name": "org/repo"}, "pull_request": {"number": 3}})
    )
    return path


def test_action_without_provider_input_names_the_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_work: list[str]
) -> None:
    """The Action has no `config init`; it points at its own `with:` inputs."""
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(_event(tmp_path)))
    monkeypatch.delenv("INPUT_PROVIDER", raising=False)
    monkeypatch.delenv("INPUT_MODEL", raising=False)

    result = CliRunner().invoke(main, ["action"])

    assert result.exit_code != 0
    assert "No provider configured" in result.output
    assert "`provider` and `model` inputs" in result.output
    assert ".lgtmaybe.yml" in result.output
    assert "config init" not in result.output
    assert "Traceback" not in result.output
    assert no_work == []


def test_gitlab_ci_without_provider_points_at_flags_and_repo_config(
    monkeypatch: pytest.MonkeyPatch, no_work: list[str]
) -> None:
    monkeypatch.setenv("CI_SERVER_URL", "https://gitlab.example.com")
    monkeypatch.setenv("CI_PROJECT_PATH", "group/project")
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", "7")

    result = CliRunner().invoke(main, ["gitlab-ci"])

    assert result.exit_code != 0
    assert "No provider configured" in result.output
    assert "--provider" in result.output
    assert ".lgtmaybe.yml" in result.output
    assert "config init" not in result.output
    assert no_work == []


def test_comment_without_provider_points_at_flags_and_repo_config(
    tmp_path: Path, no_work: list[str]
) -> None:
    result = CliRunner().invoke(main, ["comment", "--event-path", str(_event(tmp_path))])

    assert result.exit_code != 0
    assert "No provider configured" in result.output
    assert ".lgtmaybe.yml" in result.output
    assert no_work == []
