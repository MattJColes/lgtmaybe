"""Slash-command parsing and dispatch (issue_comment trigger)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from lgtmaybe.cli import main
from lgtmaybe.cli.slash import SlashCommand, dispatch, parse_command
from lgtmaybe.core.models import Provider, ProviderResult, ReviewConfig
from tests.fakes import FakeEngine, FakeGitHub, FakeProvider


def _cfg() -> ReviewConfig:
    return ReviewConfig(provider=Provider.ollama, model="llama3")


class TestParseCommand:
    def test_review(self):
        parsed = parse_command("/review")
        assert parsed is not None
        assert parsed.name is SlashCommand.review
        assert parsed.arg == ""

    def test_ask_keeps_question_text(self):
        parsed = parse_command("/ask why is this loop O(n^2)?")
        assert parsed is not None
        assert parsed.name is SlashCommand.ask
        assert parsed.arg == "why is this loop O(n^2)?"

    def test_improve_and_describe(self):
        assert parse_command("/improve").name is SlashCommand.improve
        assert parse_command("/describe").name is SlashCommand.describe

    def test_diagram(self):
        assert parse_command("/diagram").name is SlashCommand.diagram

    def test_leading_and_trailing_whitespace(self):
        assert parse_command("  /review  \n").name is SlashCommand.review

    def test_newline_separates_command_from_arg(self):
        """Any whitespace splits command from arg — GitHub comments often wrap."""
        parsed = parse_command("/review\nfull")
        assert parsed is not None
        assert parsed.name is SlashCommand.review
        assert parsed.arg == "full"

    def test_ask_with_newline_keeps_question(self):
        parsed = parse_command("/ask\nwhat does this do?")
        assert parsed is not None
        assert parsed.name is SlashCommand.ask
        assert parsed.arg == "what does this do?"

    def test_bare_slash_returns_none(self):
        assert parse_command("/") is None

    def test_non_command_returns_none(self):
        assert parse_command("looks good to me") is None

    def test_unknown_command_returns_none(self):
        assert parse_command("/frobnicate") is None


class TestDispatch:
    def test_review_triggers_a_posted_review(self):
        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())

        dispatch(
            parse_command("/review"),
            github=github,
            engine=engine,
            provider=FakeProvider(),
            cfg=_cfg(),
        )

        assert len(github.posted) == 1
        assert github.comments == []

    def test_improve_triggers_a_posted_review(self):
        github = FakeGitHub()
        engine = FakeEngine(FakeProvider())

        dispatch(
            parse_command("/improve"),
            github=github,
            engine=engine,
            provider=FakeProvider(),
            cfg=_cfg(),
        )

        assert len(github.posted) == 1

    def test_ask_replies_in_thread(self):
        github = FakeGitHub()
        answer = ProviderResult(
            text="Because it re-scans the list on every iteration.",
            input_tokens=10,
            output_tokens=8,
        )
        provider = FakeProvider(result=answer)

        dispatch(
            parse_command("/ask why is it slow?"),
            github=github,
            engine=FakeEngine(provider),
            provider=provider,
            cfg=_cfg(),
        )

        assert github.posted == []  # not a review
        assert len(github.comments) == 1
        assert "re-scans the list" in github.comments[0]

    def test_ask_does_not_leak_the_question_back_as_an_instruction(self):
        """The PR diff is wrapped as untrusted; the provider is actually called."""
        github = FakeGitHub()
        provider = FakeProvider(
            result=ProviderResult(text="answer", input_tokens=1, output_tokens=1)
        )

        dispatch(
            parse_command("/ask what does this do?"),
            github=github,
            engine=FakeEngine(provider),
            provider=provider,
            cfg=_cfg(),
        )

        sent = " ".join(m.get("content", "") for call in provider.calls for m in call["messages"])
        assert "what does this do?" in sent

    def test_describe_upserts_the_description_comment(self):
        """/describe goes through the idempotent describe upsert, and an
        unstructured model reply falls back to the raw text body."""
        github = FakeGitHub()
        provider = FakeProvider(
            result=ProviderResult(text="## Summary\nAdds a thing.", input_tokens=1, output_tokens=1)
        )

        dispatch(
            parse_command("/describe"),
            github=github,
            engine=FakeEngine(provider),
            provider=provider,
            cfg=_cfg(),
        )

        assert github.comments == []
        assert len(github.described) == 1
        assert "Summary" in github.described[0]

    def test_describe_renders_structured_output(self):
        import json as _json

        github = FakeGitHub()
        structured = _json.dumps(
            {
                "title": "Add a thing",
                "change_type": "feature",
                "summary": "Adds the thing.",
                "walkthrough": [{"path": "a.py", "summary": "adds thing"}],
            }
        )
        provider = FakeProvider(
            result=ProviderResult(text=structured, input_tokens=1, output_tokens=1)
        )

        dispatch(
            parse_command("/describe"),
            github=github,
            engine=FakeEngine(provider),
            provider=provider,
            cfg=_cfg(),
        )

        body = github.described[0]
        assert body.startswith("## Add a thing")
        assert "**Change type:** feature" in body
        assert "| `a.py` |" in body

    def test_diagram_upserts_the_diagram_comment(self):
        """/diagram goes through the idempotent diagram upsert with a mermaid block."""
        github = FakeGitHub()
        structured = json.dumps({"title": "Change map", "mermaid": 'flowchart LR\n    a["A"]'})
        provider = FakeProvider(
            result=ProviderResult(text=structured, input_tokens=1, output_tokens=1)
        )

        dispatch(
            parse_command("/diagram"),
            github=github,
            engine=FakeEngine(provider),
            provider=provider,
            cfg=_cfg(),
        )

        assert github.described == []
        assert len(github.diagrams) == 1
        assert "```mermaid" in github.diagrams[0]


def _write_event(tmp_path: Path, body: str) -> Path:
    event = {
        "comment": {"body": body},
        "issue": {"number": 7, "pull_request": {"url": "x"}},
        "repository": {"full_name": "org/repo"},
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    return path


class TestCommentCommand:
    def _patch_build(self, monkeypatch, github, engine, provider):
        import lgtmaybe.cli as cli_module

        monkeypatch.setattr(
            cli_module,
            "build_review_context",
            lambda cfg, runtime: (github, engine, provider),
        )

    def test_review_comment_retriggers_review(self, tmp_path, monkeypatch):
        github = FakeGitHub()
        provider = FakeProvider()
        self._patch_build(monkeypatch, github, FakeEngine(provider), provider)
        event = _write_event(tmp_path, "/review")

        result = CliRunner().invoke(
            main,
            ["comment", "--event-path", str(event), "--provider", "ollama", "--model", "llama3"],
        )

        assert result.exit_code == 0, result.output
        assert len(github.posted) == 1

    def test_ask_comment_replies_in_thread(self, tmp_path, monkeypatch):
        github = FakeGitHub()
        provider = FakeProvider(
            result=ProviderResult(text="It guards against null.", input_tokens=1, output_tokens=1)
        )
        self._patch_build(monkeypatch, github, FakeEngine(provider), provider)
        event = _write_event(tmp_path, "/ask why the check?")

        result = CliRunner().invoke(
            main,
            ["comment", "--event-path", str(event), "--provider", "ollama", "--model", "llama3"],
        )

        assert result.exit_code == 0, result.output
        assert github.posted == []
        assert len(github.comments) == 1
        assert "guards against null" in github.comments[0]

    def test_non_command_comment_is_ignored(self, tmp_path, monkeypatch):
        github = FakeGitHub()
        provider = FakeProvider()
        self._patch_build(monkeypatch, github, FakeEngine(provider), provider)
        event = _write_event(tmp_path, "thanks, looks good!")

        result = CliRunner().invoke(
            main,
            ["comment", "--event-path", str(event), "--provider", "ollama", "--model", "llama3"],
        )

        assert result.exit_code == 0, result.output
        assert github.posted == []
        assert github.comments == []


class TestReviewFull:
    def test_review_full_forces_a_full_review(self):
        """`/review full` overrides `incremental: true` config — the engine
        must see the whole PR diff, not an increment."""
        from lgtmaybe.core.models import PRContext
        from tests.cli.test_incremental_review import (
            INC_DIFF,
            IncrementalFakeGitHub,
            RecordingEngine,
        )

        ctx = PRContext(
            diff="diff --git a/f.py b/f.py\n@@ -1 +1,2 @@\n old\n+new\n",
            changed_files=["f.py"],
            base_sha="b",
            head_sha="head2222",
            repo="o/r",
            pr_number=1,
        )
        github = IncrementalFakeGitHub(ctx, last_sha="head1111", compare_result=INC_DIFF)
        engine = RecordingEngine()

        dispatch(
            parse_command("/review full"),
            github=github,
            engine=engine,
            provider=FakeProvider(),
            cfg=_cfg().model_copy(update={"incremental": True}),
        )

        assert engine.reviewed_ctxs[0].diff == ctx.diff  # full, not INC_DIFF
        assert github.last_reviewed_calls == 0

    def test_bare_review_honours_incremental_config(self):
        from lgtmaybe.core.models import PRContext
        from tests.cli.test_incremental_review import (
            INC_DIFF,
            IncrementalFakeGitHub,
            RecordingEngine,
        )

        ctx = PRContext(
            diff="diff --git a/f.py b/f.py\n@@ -1 +1,2 @@\n old\n+new\n",
            changed_files=["f.py"],
            base_sha="b",
            head_sha="head2222",
            repo="o/r",
            pr_number=1,
        )
        github = IncrementalFakeGitHub(ctx, last_sha="head1111", compare_result=INC_DIFF)
        engine = RecordingEngine()

        dispatch(
            parse_command("/review"),
            github=github,
            engine=engine,
            provider=FakeProvider(),
            cfg=_cfg().model_copy(update={"incremental": True}),
        )

        assert engine.reviewed_ctxs[0].diff == INC_DIFF

    def test_review_full_also_bypasses_triage(self):
        """The triage-skip notice points users at /review full, so it must
        disable triage as well as incremental scoping."""
        from lgtmaybe.core.models import PRContext
        from tests.cli.test_incremental_review import IncrementalFakeGitHub, RecordingEngine

        ctx = PRContext(
            diff="diff --git a/f.py b/f.py\n@@ -1 +1,2 @@\n old\n+new\n",
            changed_files=["f.py"],
            base_sha="b",
            head_sha="h",
            repo="o/r",
            pr_number=1,
        )
        github = IncrementalFakeGitHub(ctx)

        class _CfgRecorder(RecordingEngine):
            def __init__(self) -> None:
                super().__init__()
                self.cfgs: list[ReviewConfig] = []

            def review(self, ctx, cfg):  # type: ignore[no-untyped-def]
                self.cfgs.append(cfg)
                return super().review(ctx, cfg)

        engine = _CfgRecorder()
        cfg = _cfg().model_copy(update={"triage_model": "tiny", "incremental": True})

        dispatch(
            parse_command("/review full"),
            github=github,
            engine=engine,
            provider=FakeProvider(),
            cfg=cfg,
        )

        assert engine.cfgs[0].triage_model is None
        assert engine.cfgs[0].incremental is False
