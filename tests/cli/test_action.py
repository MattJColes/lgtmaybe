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

        monkeypatch.setattr(
            cli_module,
            "build_review_context",
            lambda cfg, runtime: (github, engine, FakeProvider()),
        )

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

    def _run_diagram_gate(self, tmp_path, monkeypatch, *, action: str, auto: str) -> list[bool]:
        """Run the action for a PR event and record whether run_diagram fired."""
        import lgtmaybe.cli as cli_module

        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())
        monkeypatch.setattr(
            cli_module,
            "build_review_context",
            lambda cfg, runtime: (github, engine, FakeProvider()),
        )

        called: list[bool] = []
        monkeypatch.setattr(
            cli_module,
            "run_diagram",
            lambda github, provider, cfg, ctx=None: called.append(True),
        )

        event = _write_event(
            tmp_path,
            {
                "action": action,
                "repository": {"full_name": "org/repo"},
                "pull_request": {"number": 3},
            },
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        monkeypatch.setenv("INPUT_AUTO_DIAGRAM", auto)

        result = CliRunner().invoke(main, ["action"])
        assert result.exit_code == 0, result.output
        # The review still runs regardless of the diagram.
        assert len(github.posted) == 1
        return called

    def test_auto_diagram_posts_on_opened_pr(self, tmp_path, monkeypatch):
        called = self._run_diagram_gate(tmp_path, monkeypatch, action="opened", auto="true")
        assert called == [True]

    def test_auto_diagram_skipped_on_synchronize(self, tmp_path, monkeypatch):
        called = self._run_diagram_gate(tmp_path, monkeypatch, action="synchronize", auto="true")
        assert called == []

    def test_auto_diagram_on_by_default(self, tmp_path, monkeypatch):
        called = self._run_diagram_gate(tmp_path, monkeypatch, action="opened", auto="")
        assert called == [True]

    def test_auto_diagram_can_be_disabled(self, tmp_path, monkeypatch):
        called = self._run_diagram_gate(tmp_path, monkeypatch, action="opened", auto="false")
        assert called == []

    def test_auto_extras_share_one_gateway_and_context_fetch(self, tmp_path, monkeypatch):
        """With auto_describe + auto_diagram on, the action builds the adapters
        once and fetches the (expensive, O(files)) PR context once — describe,
        diagram, and the review all reuse them."""
        import lgtmaybe.cli as cli_module

        class _CountingGitHub(FakeGitHub):
            def __init__(self) -> None:
                super().__init__()
                self.context_fetches = 0

            def get_pr_context(self):
                self.context_fetches += 1
                return super().get_pr_context()

        github = _CountingGitHub()
        provider = FakeProvider()
        builds: list[int] = []

        def fake_build(cfg, runtime):
            builds.append(1)
            return github, FakeEngine(provider), provider

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

        event = _write_event(
            tmp_path,
            {
                "action": "opened",
                "repository": {"full_name": "org/repo"},
                "pull_request": {"number": 3},
            },
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        monkeypatch.setenv("INPUT_AUTO_DESCRIBE", "true")
        monkeypatch.setenv("INPUT_AUTO_DIAGRAM", "true")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert builds == [1]
        assert github.context_fetches == 1
        assert len(github.described) == 1
        assert len(github.diagrams) == 1
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

    def _run_reply(
        self,
        tmp_path,
        monkeypatch,
        *,
        comment: dict,
        action: str = "created",
        thread: tuple[str, str] | None,
        answer_replies: str | None = None,
    ) -> FakeGitHub:
        """Run the action for a pull_request_review_comment event; return the gateway."""
        import lgtmaybe.cli as cli_module

        github = FakeGitHub()
        github.thread = thread
        provider = FakeProvider()
        monkeypatch.setattr(
            cli_module,
            "build_review_context",
            lambda cfg, runtime: (github, FakeEngine(provider), provider),
        )

        event = _write_event(
            tmp_path,
            {
                "action": action,
                "repository": {"full_name": "org/repo"},
                "pull_request": {"number": 7},
                "comment": comment,
            },
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_review_comment")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        if answer_replies is not None:
            monkeypatch.setenv("INPUT_ANSWER_REPLIES", answer_replies)

        result = CliRunner().invoke(main, ["action"])
        assert result.exit_code == 0, result.output
        return github

    _OURS = ("THREAD_1", "**[HIGH] NPE**\n\nbody\n\n<!-- lgtmaybe-finding:abc123def456 -->")
    _HUMAN_REPLY = {
        "in_reply_to_id": 555,
        "path": "a.py",
        "line": 1,
        "body": "is this really a bug?",
        "user": {"type": "User", "login": "alice"},
    }

    def test_review_comment_reply_in_our_thread_is_answered(self, tmp_path, monkeypatch):
        github = self._run_reply(
            tmp_path, monkeypatch, comment=self._HUMAN_REPLY, thread=self._OURS
        )
        assert len(github.replies) == 1
        assert github.replies[0][0] == "THREAD_1"

    def test_review_comment_ignored_when_not_a_reply(self, tmp_path, monkeypatch):
        top_level = {**self._HUMAN_REPLY}
        del top_level["in_reply_to_id"]
        github = self._run_reply(tmp_path, monkeypatch, comment=top_level, thread=self._OURS)
        assert github.replies == []

    def test_review_comment_ignored_when_parent_not_ours(self, tmp_path, monkeypatch):
        not_ours = ("THREAD_1", "a plain human review comment, no marker")
        github = self._run_reply(tmp_path, monkeypatch, comment=self._HUMAN_REPLY, thread=not_ours)
        assert github.replies == []

    def test_review_comment_ignored_when_author_is_a_bot(self, tmp_path, monkeypatch):
        bot_reply = {**self._HUMAN_REPLY, "user": {"type": "Bot", "login": "lgtmaybe[bot]"}}
        github = self._run_reply(tmp_path, monkeypatch, comment=bot_reply, thread=self._OURS)
        assert github.replies == []

    def test_review_comment_ignored_when_action_is_not_created(self, tmp_path, monkeypatch):
        github = self._run_reply(
            tmp_path, monkeypatch, comment=self._HUMAN_REPLY, thread=self._OURS, action="edited"
        )
        assert github.replies == []

    def test_review_comment_ignored_when_answer_replies_disabled(self, tmp_path, monkeypatch):
        github = self._run_reply(
            tmp_path,
            monkeypatch,
            comment=self._HUMAN_REPLY,
            thread=self._OURS,
            answer_replies="false",
        )
        assert github.replies == []

    def test_inputs_read_from_env_reach_config(self, tmp_path, monkeypatch):
        """INPUT_PROVIDER / INPUT_MODEL select the provider+model for the run."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["provider"] = cfg.provider.value
            captured["model"] = cfg.model
            captured["fallback_model"] = runtime.fallback_model
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

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
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

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
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

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

    def test_fail_on_input_reaches_config(self, tmp_path, monkeypatch):
        """INPUT_FAIL_ON sets the merge-gate threshold from the Action."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["fail_on"] = cfg.fail_on
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        monkeypatch.setenv("INPUT_FAIL_ON", "high")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"fail_on": "high"}

    def test_num_ctx_and_max_input_tokens_inputs_reach_config(self, tmp_path, monkeypatch):
        """INPUT_NUM_CTX / INPUT_MAX_INPUT_TOKENS tune a big-diff run from the Action."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["num_ctx"] = cfg.num_ctx
            captured["max_input_tokens"] = cfg.max_input_tokens
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

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
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

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

    def test_config_path_input_missing_file_errors(self, tmp_path, monkeypatch):
        """An action-configured config path that doesn't exist fails loudly —
        a typo'd config_path must not silently run with defaults."""
        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        monkeypatch.setenv("INPUT_CONFIG_PATH", str(tmp_path / "mytea.yml"))

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code != 0
        assert "not found" in result.output

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

    def test_static_analysis_and_profile_inputs_accept_pydantic_style_bools(
        self, tmp_path, monkeypatch
    ):
        """static_analysis and profile share one bool parser that accepts the
        same spellings pydantic does for the other bool inputs (incl. "on")."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["static_analysis"] = cfg.static_analysis.enabled
            captured["profile"] = runtime.profile
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        monkeypatch.setenv("INPUT_STATIC_ANALYSIS", "on")
        monkeypatch.setenv("INPUT_PROFILE", "On")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"static_analysis": True, "profile": True}

    def test_parse_bool_helper_matches_pydantic_spellings(self):
        from lgtmaybe.cli.commands import _parse_bool

        assert _parse_bool(None) is None
        for truthy in ("true", "True", "1", "yes", "y", "on", "t"):
            assert _parse_bool(truthy) is True, truthy
        for falsy in ("false", "0", "no", "off", "nonsense"):
            assert _parse_bool(falsy) is False, falsy

    def test_azure_api_base_input_reaches_runtime(self, tmp_path, monkeypatch):
        """INPUT_API_BASE carries the azure resource endpoint into the run."""
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["api_base"] = runtime.api_base
            captured["api_key"] = runtime.api_key
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

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
