"""The `action` entrypoint: the GitHub Action container's command.

It reads inputs from ``INPUT_*`` env vars and routes by ``GITHUB_EVENT_NAME`` —
``pull_request`` / ``pull_request_target`` run a review, ``issue_comment`` routes
a slash command. The PR URL for a review is derived from the event payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from lgtmaybe.cli import RuntimeOptions, execute_comment, main, pr_url_from_event
from lgtmaybe.core.models import Provider, ReviewConfig
from tests.fakes import FakeEngine, FakeGitHub, FakeProvider


def test_execute_comment_missing_repository_raises_clean_error():
    """An issue_comment payload missing ``repository`` surfaces a ClickException."""
    import click

    event = {
        "comment": {"body": "/review"},
        "issue": {"pull_request": {"url": "x"}, "number": 5},
        # no "repository" key
    }
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    with pytest.raises(click.ClickException, match="missing required field"):
        execute_comment(event, cfg, RuntimeOptions())


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

    def test_missing_field_raises_clean_click_exception(self):
        """A malformed event payload surfaces a clear ClickException, not KeyError."""
        import click

        with pytest.raises(click.ClickException, match="missing required field"):
            pr_url_from_event({"pull_request": {"number": 7}})  # no "repository"


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

    def test_reflect_model_input_reaches_config(self, tmp_path, monkeypatch):
        """INPUT_REFLECT_MODEL selects the reflection-pass model from the Action."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["reflect_model"] = cfg.reflect_model
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
        monkeypatch.setenv("INPUT_REFLECT_MODEL", "bigger-judge")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"reflect_model": "bigger-judge"}

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

    def test_config_path_input_selects_the_repo_config(self, tmp_path, monkeypatch):
        """INPUT_CONFIG_PATH points the run at a custom repo config file."""
        cfg_file = tmp_path / "custom.yml"
        cfg_file.write_text("min_severity: high\n")
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["min_severity"] = cfg.min_severity.value
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
        monkeypatch.setenv("INPUT_CONFIG_PATH", str(cfg_file))

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"min_severity": "high"}

    def test_config_path_input_defaults_when_empty(self, monkeypatch):
        """An unset or empty INPUT_CONFIG_PATH normalises to None like every
        other input; the action falls back to .lgtmaybe.yml."""
        from lgtmaybe.cli import action_inputs

        monkeypatch.delenv("INPUT_CONFIG_PATH", raising=False)
        assert action_inputs()["config_path"] is None

        monkeypatch.setenv("INPUT_CONFIG_PATH", "")
        assert action_inputs()["config_path"] is None

    def test_structured_output_input_is_read(self, monkeypatch):
        """INPUT_STRUCTURED_OUTPUT is the action's escape hatch for a gateway that
        rejects response_format (issue #104); empty/unset normalises to None."""
        from lgtmaybe.cli import action_inputs

        monkeypatch.delenv("INPUT_STRUCTURED_OUTPUT", raising=False)
        assert action_inputs()["structured_output"] is None

        monkeypatch.setenv("INPUT_STRUCTURED_OUTPUT", "false")
        assert action_inputs()["structured_output"] == "false"

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
