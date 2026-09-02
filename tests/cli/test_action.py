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
            lambda github, provider, cfg, ctx=None, completed_sha=None: called.append(True),
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

    def test_auto_diagram_posts_on_synchronize(self, tmp_path, monkeypatch):
        called = self._run_diagram_gate(tmp_path, monkeypatch, action="synchronize", auto="true")
        assert called == [True]

    def test_auto_diagram_on_by_default(self, tmp_path, monkeypatch):
        called = self._run_diagram_gate(tmp_path, monkeypatch, action="opened", auto="")
        assert called == [True]

    def test_auto_diagram_can_be_disabled(self, tmp_path, monkeypatch):
        called = self._run_diagram_gate(tmp_path, monkeypatch, action="opened", auto="false")
        assert called == []

    def test_nothing_auto_posts_when_the_overview_is_off(self, tmp_path, monkeypatch):
        """auto_diagram off is the one switch: with no overview there is no
        automatic description either, since it rides that comment."""
        import lgtmaybe.cli as cli_module

        github = FakeGitHub()
        provider = FakeProvider()
        monkeypatch.setattr(
            cli_module,
            "build_review_context",
            lambda cfg, runtime: (github, FakeEngine(provider), provider),
        )

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
        monkeypatch.setenv("INPUT_AUTO_DIAGRAM", "false")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert len(github.posted) == 1
        assert github.diagrams == []
        assert github.described == []

    def test_the_overview_shares_one_gateway_and_context_fetch(self, tmp_path, monkeypatch):
        """The action builds the adapters once and fetches the (expensive,
        O(files)) PR context once — the review and the whole change overview
        reuse them."""
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
        monkeypatch.setenv("INPUT_AUTO_DIAGRAM", "true")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert builds == [1]
        assert github.context_fetches == 1
        assert len(github.posted) == 1
        # One comment carries every section — the standalone description
        # comment belongs to /describe alone now.
        assert github.described == []
        assert len(github.diagrams) == 1
        assert "### **High Impact Areas**" in github.diagrams[0]

    def test_the_overview_posts_after_the_review(self, tmp_path, monkeypatch):
        """The comments lgtmaybe posts itself go out after the review, never before.

        Posting a comment fires an ``issue_comment`` workflow run. A consumer whose
        concurrency group isn't discriminated by the event name puts that run in the
        same group as the review, so ``cancel-in-progress`` kills the review that
        posted the comment. Posting last makes such a cancellation harmless instead
        of fatal — the review is already on the PR.
        """
        import lgtmaybe.cli as cli_module

        class _OrderedGitHub(FakeGitHub):
            def __init__(self) -> None:
                super().__init__()
                self.writes: list[str] = []

            def post_review(self, findings, summary, diff=None):
                self.writes.append("review")
                super().post_review(findings, summary, diff)

            def post_describe_comment(self, body: str) -> None:
                self.writes.append("describe")
                super().post_describe_comment(body)

            def post_diagram_comment(self, body: str, *, completed_sha: str | None = None) -> None:
                self.writes.append("diagram")
                super().post_diagram_comment(body, completed_sha=completed_sha)

        github = _OrderedGitHub()
        provider = FakeProvider()
        monkeypatch.setattr(
            cli_module,
            "build_review_context",
            lambda cfg, runtime: (github, FakeEngine(provider), provider),
        )

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
        monkeypatch.setenv("INPUT_AUTO_DIAGRAM", "true")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert github.writes == ["review", "diagram"]
        marker = f"<!-- lgtmaybe-diagrammed:{github.get_pr_context().head_sha} -->"
        assert marker in github.diagrams[0]

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

    def test_review_comment_event_is_a_noop_before_configuration(self, tmp_path, monkeypatch):
        import lgtmaybe.cli.commands as commands_module

        event = _write_event(tmp_path, {"action": "created"})
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_review_comment")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setattr(
            commands_module,
            "action_inputs",
            lambda: pytest.fail("stale review-comment events must not read Action inputs"),
        )

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output

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

    def test_max_tokens_input_reaches_config(self, tmp_path, monkeypatch):
        """INPUT_MAX_TOKENS caps generation from the Action.

        The cap exists for prepaid routes that reserve prompt + max_tokens against
        the balance before generating, and the Action is where those reviews
        actually run — a knob wired only into the local CLI would leave the case
        it was built for uncapped. Arrives as the env var's string, so this also
        pins the coercion to int.
        """
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["max_tokens"] = cfg.max_tokens
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "openrouter")
        monkeypatch.setenv("INPUT_MODEL", "vendor/m")
        monkeypatch.setenv("INPUT_MAX_TOKENS", "8192")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"max_tokens": 8192}

    def test_reasoning_effort_input_reaches_config(self, tmp_path, monkeypatch):
        """INPUT_REASONING_EFFORT bounds thinking from the Action.

        The Action is where the problem was measured — this repo's own dogfood
        review truncated 5 of 9 lens calls on reasoning alone — so a knob wired
        only into the local CLI would miss the case it exists for.
        """
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["reasoning_effort"] = cfg.reasoning_effort
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "openrouter")
        monkeypatch.setenv("INPUT_MODEL", "vendor/m")
        monkeypatch.setenv("INPUT_REASONING_EFFORT", "low")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"reasoning_effort": "low"}

    def test_reasoning_effort_input_absent_stays_unset(self, tmp_path, monkeypatch):
        """Action inputs default to "" — which must stay None, not become a
        literal empty effort the provider would reject.

        Run from an empty directory: the config probe reads ``.lgtmaybe.yml``
        relative to the cwd, and lgtmaybe's own now sets ``reasoning_effort``, so
        a suite run from the repo root would test that file instead of input
        coercion.
        """
        monkeypatch.chdir(tmp_path)
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["reasoning_effort"] = cfg.reasoning_effort
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "openrouter")
        monkeypatch.setenv("INPUT_MODEL", "vendor/m")
        monkeypatch.setenv("INPUT_REASONING_EFFORT", "")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"reasoning_effort": None}

    def test_max_tokens_input_absent_leaves_generation_uncapped(self, tmp_path, monkeypatch):
        """An empty/unset input must stay None, not become a cap — the Action's
        inputs default to "" and a coerced 0 would reject every request.

        Run from an empty directory: the config probe reads ``.lgtmaybe.yml``
        relative to the cwd, and lgtmaybe's own sets ``max_tokens``, so a suite
        run from the repo root would test that file instead of input coercion.
        """
        monkeypatch.chdir(tmp_path)
        captured: dict[str, object] = {}

        import lgtmaybe.cli as cli_module

        def fake_build(cfg, runtime):
            captured["max_tokens"] = cfg.max_tokens
            return FakeGitHub(), FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", fake_build)

        event = _write_event(
            tmp_path,
            {"repository": {"full_name": "org/repo"}, "pull_request": {"number": 1}},
        )
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.setenv("INPUT_PROVIDER", "openrouter")
        monkeypatch.setenv("INPUT_MODEL", "vendor/m")
        monkeypatch.setenv("INPUT_MAX_TOKENS", "")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code == 0, result.output
        assert captured == {"max_tokens": None}

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

    def test_action_inputs_are_derived_from_review_config(self):
        """`action_inputs()` reads a written-out name list, not ReviewConfig's fields.

        Deriving the names from ``ReviewConfig.model_fields`` would silently
        accept INPUT_* vars `action.yml` never declares — so a config field with
        no matching action input must stay unreadable from the environment.
        """
        from lgtmaybe.cli import _ACTION_CONFIG_EXCLUSIONS, _RUNTIME_INPUTS, action_inputs
        from lgtmaybe.core.models import ReviewConfig

        assert set(action_inputs()) == (
            set(ReviewConfig.model_fields) - _ACTION_CONFIG_EXCLUSIONS | _RUNTIME_INPUTS
        )

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
