"""The `action` entrypoint: the GitHub Action container's command.

It reads inputs from ``INPUT_*`` env vars and routes by ``GITHUB_EVENT_NAME`` —
``pull_request`` / ``pull_request_target`` run a review, ``issue_comment`` routes
a slash command. The PR URL for a review is derived from the event payload.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from lgtmaybe.cli import main, pr_url_from_event
from tests.fakes import FakeEngine, FakeGitHub, FakeProvider


class TestPrUrlFromEvent:
    def test_builds_url_from_repository_and_number(self):
        event = {
            "repository": {"full_name": "org/my-repo"},
            "pull_request": {"number": 42},
        }
        assert pr_url_from_event(event) == "https://github.com/org/my-repo/pull/42"

    def test_honours_github_server_url(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://ghe.example.com")
        event = {
            "repository": {"full_name": "org/repo"},
            "pull_request": {"number": 7},
        }
        assert pr_url_from_event(event) == "https://ghe.example.com/org/repo/pull/7"


def _write_event(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload))
    return path


class TestActionRouting:
    def test_pull_request_event_runs_a_review(self, tmp_path, monkeypatch):
        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())

        import lgtmaybe.cli as cli_module

        monkeypatch.setattr(cli_module, "build_adapters", lambda cfg, runtime: (github, engine))

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 3}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert len(github.posted) == 1

    def test_issue_comment_event_routes_slash_command(self, tmp_path, monkeypatch):
        github = FakeGitHub()
        provider = FakeProvider()

        import lgtmaybe.cli as cli_module

        monkeypatch.setattr(
            cli_module,
            "build_review_context",
            lambda cfg, runtime: (github, FakeEngine(provider), provider),
        )

        event = _write_event(
            tmp_path,
            {
                "comment": {"body": "/review"},
                "issue": {"number": 9, "pull_request": {"url": "x"}},
                "repository": {"full_name": "org/repo"},
            },
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert len(github.posted) == 1

    def test_inputs_read_from_env_reach_config(self, tmp_path, monkeypatch):
        """INPUT_PROVIDER / INPUT_MODEL select the provider+model for the run."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["provider"] = cfg.provider.value
            captured["model"] = cfg.model
            captured["fallback_model"] = runtime.fallback_model
            return FakeGitHub(), FakeEngine(FakeProvider())

        monkeypatch.setattr(cli_module, "build_adapters", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "anthropic")
        monkeypatch.setenv("INPUT_MODEL", "claude-3-5-sonnet")
        monkeypatch.setenv("INPUT_FALLBACK_MODEL", "claude-3-haiku")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {
            "provider": "anthropic",
            "model": "claude-3-5-sonnet",
            "fallback_model": "claude-3-haiku",
        }

    def test_timeout_and_temperature_inputs_reach_config(self, tmp_path, monkeypatch):
        """INPUT_TIMEOUT / INPUT_TEMPERATURE tune the run from the Action."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["timeout"] = cfg.timeout
            captured["temperature"] = cfg.temperature
            return FakeGitHub(), FakeEngine(FakeProvider())

        monkeypatch.setattr(cli_module, "build_adapters", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        monkeypatch.setenv("INPUT_TIMEOUT", "900")
        monkeypatch.setenv("INPUT_TEMPERATURE", "0.2")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"timeout": 900, "temperature": 0.2}

    def test_num_ctx_and_max_input_tokens_inputs_reach_config(self, tmp_path, monkeypatch):
        """INPUT_NUM_CTX / INPUT_MAX_INPUT_TOKENS tune a big-diff run from the Action."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["num_ctx"] = cfg.num_ctx
            captured["max_input_tokens"] = cfg.max_input_tokens
            return FakeGitHub(), FakeEngine(FakeProvider())

        monkeypatch.setattr(cli_module, "build_adapters", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        monkeypatch.setenv("INPUT_NUM_CTX", "32768")
        monkeypatch.setenv("INPUT_MAX_INPUT_TOKENS", "250000")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"num_ctx": 32768, "max_input_tokens": 250000}

    def test_azure_api_base_input_reaches_runtime(self, tmp_path, monkeypatch):
        """INPUT_API_BASE carries the azure resource endpoint into the run."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["api_base"] = runtime.api_base
            captured["api_key"] = runtime.api_key
            return FakeGitHub(), FakeEngine(FakeProvider())

        monkeypatch.setattr(cli_module, "build_adapters", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "azure")
        monkeypatch.setenv("INPUT_MODEL", "gpt-4o")
        monkeypatch.setenv("INPUT_API_KEY", "azure-secret")
        monkeypatch.setenv("INPUT_API_BASE", "https://my-resource.openai.azure.com")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {
            "api_base": "https://my-resource.openai.azure.com",
            "api_key": "azure-secret",
        }
