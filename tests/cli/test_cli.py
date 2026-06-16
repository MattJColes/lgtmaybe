"""Tests for the CLI entry point and run_review logic."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from lgtmaybe.cli import RuntimeOptions, build_adapters, main, run_review
from lgtmaybe.core.models import PRContext, ReviewConfig, ReviewFinding
from lgtmaybe.core.ports import ReviewEngine
from tests.fakes import FakeEngine, FakeGitHub, FakeProvider


class _BoomEngine(ReviewEngine):
    """A ReviewEngine that always fails — used to exercise error surfacing."""

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        raise RuntimeError("provider exploded")


def _default_cfg(**overrides: object) -> ReviewConfig:
    base = {"provider": "ollama", "model": "llama3"}
    base.update(overrides)
    return ReviewConfig.model_validate(base)


_LOCAL_CTX = PRContext(
    diff="@@ -1 +1 @@\n-a\n+b\n",
    changed_files=["src/app.py"],
    base_sha="base",
    head_sha="head",
    repo="org/repo",
    pr_number=0,
)


def _patch_local(monkeypatch, engine=None):
    """Wire the local review command onto fakes: fake provider/engine + git context."""
    import lgtmaybe.cli as cli_module

    engine = engine if engine is not None else FakeEngine(FakeProvider())
    monkeypatch.setattr(cli_module, "build_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(cli_module, "LLMReviewEngine", lambda provider: engine)
    monkeypatch.setattr(cli_module, "local_pr_context", lambda **kwargs: _LOCAL_CTX)


class TestRunReview:
    def test_dry_run_does_not_post(self):
        """dry_run=True must not call post_review on the github gateway."""
        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())
        cfg = _default_cfg()

        findings, summary = run_review(github=github, engine=engine, cfg=cfg, dry_run=True)

        assert github.posted == []

    def test_dry_run_returns_findings(self):
        """dry_run=True still returns findings and summary from the engine."""
        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())
        cfg = _default_cfg()

        findings, summary = run_review(github=github, engine=engine, cfg=cfg, dry_run=True)

        assert len(findings) >= 1
        assert summary != ""

    def test_non_dry_run_posts(self):
        """Without dry_run, post_review is called exactly once."""
        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())
        cfg = _default_cfg()

        run_review(github=github, engine=engine, cfg=cfg, dry_run=False)

        assert len(github.posted) == 1

    def test_non_dry_run_passes_fetched_diff_to_post_review(self):
        """post_review must receive the already-fetched diff so it doesn't
        re-fetch the entire PR context just to rebuild the commentable-line index."""
        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())
        cfg = _default_cfg()

        run_review(github=github, engine=engine, cfg=cfg, dry_run=False)

        assert github.posted_diffs == [github.get_pr_context().diff]

    def test_non_dry_run_posts_correct_findings(self):
        """Posted findings match what the engine returned."""
        github = FakeGitHub()
        provider = FakeProvider()
        engine = FakeEngine(provider)
        cfg = _default_cfg()

        findings, summary = run_review(github=github, engine=engine, cfg=cfg, dry_run=False)

        posted_findings, posted_summary = github.posted[0]
        assert posted_findings == findings
        assert posted_summary == summary


class TestReviewCommandLocal:
    def test_prints_findings_in_human_form(self, monkeypatch):
        """`review` runs the local pipeline and prints findings to stdout."""
        _patch_local(monkeypatch)

        result = CliRunner().invoke(main, ["review", "--provider", "ollama", "--model", "llama3"])

        assert result.exit_code == 0, result.output
        assert "canned finding" in result.output

    def test_json_flag_outputs_parseable_array(self, monkeypatch):
        """`review --json` emits a JSON array of findings."""
        _patch_local(monkeypatch)

        result = CliRunner().invoke(
            main, ["review", "--provider", "ollama", "--model", "llama3", "--json"]
        )

        assert result.exit_code == 0, result.output
        json_line = next(line for line in result.output.splitlines() if line.startswith("[{"))
        parsed = json.loads(json_line)
        assert isinstance(parsed, list)
        assert parsed[0]["severity"] == "low"

    def test_format_agent_outputs_correction_instructions(self, monkeypatch):
        """`review --format agent` emits directive instructions for an AI to apply."""
        _patch_local(monkeypatch)

        result = CliRunner().invoke(
            main,
            ["review", "--provider", "ollama", "--model", "llama3", "--format", "agent"],
        )

        assert result.exit_code == 0, result.output
        assert "canned finding" in result.output
        assert "apply" in result.output.lower()

    def test_does_not_require_github_token(self, monkeypatch):
        """The local review must work with no GITHUB_TOKEN in the environment."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        _patch_local(monkeypatch)

        result = CliRunner().invoke(main, ["review", "--provider", "ollama", "--model", "llama3"])

        assert result.exit_code == 0, result.output

    def test_working_and_uncommitted_flags_conflict(self, monkeypatch):
        """--working (worktree vs base) and --uncommitted (edits vs HEAD) are
        mutually exclusive — fail fast with a usage error, before any provider work."""
        _patch_local(monkeypatch)

        result = CliRunner().invoke(
            main,
            ["review", "--provider", "ollama", "--model", "llama3", "--working", "--uncommitted"],
        )

        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_uncommitted_flag_is_threaded_through(self, monkeypatch):
        """`review --uncommitted` reaches local_pr_context as uncommitted=True."""
        import lgtmaybe.cli as cli_module

        seen: dict[str, object] = {}

        def _capture(**kwargs):
            seen.update(kwargs)
            return _LOCAL_CTX

        _patch_local(monkeypatch)
        monkeypatch.setattr(cli_module, "local_pr_context", _capture)

        result = CliRunner().invoke(
            main, ["review", "--provider", "ollama", "--model", "llama3", "--uncommitted"]
        )

        assert result.exit_code == 0, result.output
        assert seen.get("uncommitted") is True
        assert seen.get("working") is False


class TestModuleEntrypoint:
    def test_python_m_lgtmaybe_runs_the_cli_group(self):
        """`python -m lgtmaybe` (Docker ENTRYPOINT) must invoke the real CLI."""
        import lgtmaybe.__main__ as entry

        assert entry.main is main

    def test_help_lists_review_and_comment_commands(self):
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "review" in result.output
        assert "comment" in result.output


class TestGitHubReviewErrorSurfacing:
    """The GitHub path (execute_review, used by the action) posts a failure notice."""

    def test_engine_failure_posts_comment_and_raises(self, monkeypatch):
        import click

        import lgtmaybe.cli as cli_module

        github = FakeGitHub()
        monkeypatch.setattr(
            cli_module, "build_adapters", lambda cfg, runtime: (github, _BoomEngine())
        )

        with pytest.raises(click.ClickException):
            cli_module.execute_review(_default_cfg(), RuntimeOptions(pr_url="x"), dry_run=False)

        assert len(github.posted) == 1
        posted_findings, posted_summary = github.posted[0]
        assert posted_findings == []
        assert "fail" in posted_summary.lower()


class TestLocalReviewErrors:
    def test_not_a_git_repo_exits_nonzero(self, monkeypatch):
        import lgtmaybe.cli as cli_module

        monkeypatch.setattr(cli_module, "build_provider", lambda *a, **k: FakeProvider())
        monkeypatch.setattr(cli_module, "LLMReviewEngine", lambda provider: FakeEngine(provider))

        def boom(**kwargs):
            raise ValueError("not a git repository")

        monkeypatch.setattr(cli_module, "local_pr_context", boom)

        result = CliRunner().invoke(main, ["review", "--provider", "ollama", "--model", "llama3"])

        assert result.exit_code != 0
        assert "not a git repository" in result.output

    def test_engine_failure_exits_nonzero(self, monkeypatch):
        _patch_local(monkeypatch, engine=_BoomEngine())

        result = CliRunner().invoke(main, ["review", "--provider", "ollama", "--model", "llama3"])

        assert result.exit_code != 0


class TestParsePrUrl:
    def test_parses_owner_repo_and_number(self):
        from lgtmaybe.cli import parse_pr_url

        repo, number = parse_pr_url("https://github.com/org/my-repo/pull/42")
        assert repo == "org/my-repo"
        assert number == 42

    def test_rejects_non_pr_url(self):
        from lgtmaybe.cli import parse_pr_url

        with pytest.raises(ValueError, match="PR URL"):
            parse_pr_url("https://github.com/org/my-repo/issues/42")


class TestBuildAdapters:
    def test_builds_real_adapters_for_ollama(self, monkeypatch):
        """build_adapters returns a RestGitHubGateway + LLMReviewEngine wired from config."""
        from lgtmaybe.engine import LLMReviewEngine
        from lgtmaybe.github import RestGitHubGateway

        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        cfg = _default_cfg(provider="ollama", model="llama3")
        runtime = RuntimeOptions(pr_url="https://github.com/org/repo/pull/7")

        github, engine = build_adapters(cfg, runtime)

        assert isinstance(github, RestGitHubGateway)
        assert isinstance(engine, LLMReviewEngine)

    def test_requires_github_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        cfg = _default_cfg(provider="ollama", model="llama3")
        runtime = RuntimeOptions(pr_url="https://github.com/org/repo/pull/7")

        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            build_adapters(cfg, runtime)

    def test_surfaces_missing_provider_credentials(self, monkeypatch):
        """An API-key provider with no key raises the resolver's clear error."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = _default_cfg(provider="openai", model="gpt-4o")
        runtime = RuntimeOptions(pr_url="https://github.com/org/repo/pull/7")

        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            build_adapters(cfg, runtime)

    def test_fallback_model_threads_to_provider(self, monkeypatch):
        """A runtime fallback_model reaches the built LiteLLMProvider."""
        from lgtmaybe.cli import build_review_context

        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        cfg = _default_cfg(provider="ollama", model="llama3")
        runtime = RuntimeOptions(
            pr_url="https://github.com/org/repo/pull/7", fallback_model="llama2"
        )

        _github, _engine, provider = build_review_context(cfg, runtime)

        assert provider.fallback_model == "ollama/llama2"

    def test_azure_keyless_ad_token_threads_to_provider(self, monkeypatch):
        """Keyless azure resolves an ambient AD token and threads it to litellm."""
        from lgtmaybe.cli import build_review_context

        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.delenv("AZURE_API_KEY", raising=False)
        monkeypatch.setattr(
            "lgtmaybe.providers.credentials._default_azure_token",
            lambda: "ad-token-from-oidc",
        )
        cfg = _default_cfg(provider="azure", model="my-deployment")
        runtime = RuntimeOptions(
            pr_url="https://github.com/org/repo/pull/7",
            api_base="https://my-resource.openai.azure.com",
        )

        _github, _engine, provider = build_review_context(cfg, runtime)

        assert provider.default_opts.get("azure_ad_token") == "ad-token-from-oidc"
        assert "api_key" not in provider.default_opts
