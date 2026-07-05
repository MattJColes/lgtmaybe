"""Orchestration of commit-scoped incremental review (P2) in run_review.

With ``cfg.incremental`` on and a gateway that can supply the last-reviewed
SHA and the compare diff, ``run_review`` reviews only the increment and posts
against the full PR diff. Every degraded case — no marker (first review),
force-push/rebase (compare returns None), same head, a gateway without the
adapter methods — falls back to the full review, byte-for-byte the old
behaviour.
"""

from __future__ import annotations

from lgtmaybe.cli import resolve_auto_incremental, run_review
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.core.ports import ReviewEngine
from tests.fakes import FakeGitHub

FULL_DIFF = (
    "diff --git a/src/app.py b/src/app.py\n@@ -1 +1,2 @@\n old\n+new\n"
    "diff --git a/src/other.py b/src/other.py\n@@ -1 +1,2 @@\n old\n+other\n"
)
INC_DIFF = "diff --git a/src/app.py b/src/app.py\n@@ -1,2 +1,3 @@\n old\n new\n+newer\n"

CTX = PRContext(
    diff=FULL_DIFF,
    changed_files=["src/app.py", "src/other.py"],
    base_sha="base0000",
    head_sha="head2222",
    repo="org/repo",
    pr_number=5,
)


def _cfg(**overrides: object) -> ReviewConfig:
    return ReviewConfig(provider=Provider.ollama, model="llama3", **overrides)  # type: ignore[arg-type]


class RecordingEngine(ReviewEngine):
    """Returns canned findings; records the ctx it was asked to review."""

    def __init__(self, findings: list[ReviewFinding] | None = None) -> None:
        self.findings = findings or []
        self.reviewed_ctxs: list[PRContext] = []

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        self.reviewed_ctxs.append(ctx)
        return list(self.findings), "1 finding · summary"


class IncrementalFakeGitHub(FakeGitHub):
    """FakeGitHub with the incremental adapter methods, recording calls."""

    def __init__(
        self,
        ctx: PRContext | None = None,
        *,
        last_sha: str | None = None,
        compare_result: str | None = None,
    ) -> None:
        super().__init__(ctx)
        self._last_sha = last_sha
        self._compare_result = compare_result
        self.compare_calls: list[tuple[str, str]] = []
        self.marked_reviewed: list[str] = []
        self.scopes: list[set[str] | None] = []
        self.last_reviewed_calls = 0

    def last_reviewed_sha(self) -> str | None:
        self.last_reviewed_calls += 1
        return self._last_sha

    def compare_diff(self, base_sha: str, head_sha: str) -> str | None:
        self.compare_calls.append((base_sha, head_sha))
        return self._compare_result

    def mark_reviewed(self, head_sha: str) -> None:
        self.marked_reviewed.append(head_sha)

    def set_incremental_scope(self, paths: set[str] | None) -> None:
        self.scopes.append(paths)


def test_incremental_reviews_only_the_increment() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)
    engine = RecordingEngine()

    _findings, summary = run_review(
        github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=False
    )

    # The engine saw only the increment; the post anchored against the full diff.
    assert engine.reviewed_ctxs[0].diff == INC_DIFF
    assert github.posted_diffs == [FULL_DIFF]
    assert "head111" in summary  # names the last-reviewed SHA (short form)
    # Resolve-on-fix restricted to the files actually re-reviewed this run.
    assert github.scopes == [{"src/app.py"}]
    assert github.marked_reviewed == ["head2222"]


def test_no_marker_falls_back_to_full_review() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha=None)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=False)

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF
    assert github.compare_calls == []
    assert github.scopes == []  # full review: resolve-on-fix unrestricted


def test_force_push_falls_back_to_full_review() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=None)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=False)

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF
    assert github.compare_calls == [("head1111", "head2222")]


def test_same_head_falls_back_to_full_review_without_compare() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head2222", compare_result=INC_DIFF)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=False)

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF
    assert github.compare_calls == []


def test_incremental_off_never_queries_the_marker() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=_cfg(), dry_run=False)  # default: auto/off

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF
    assert github.last_reviewed_calls == 0


def test_gateway_without_incremental_methods_reviews_full() -> None:
    github = FakeGitHub(CTX)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=False)

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF


def test_left_side_findings_dropped_in_incremental_mode() -> None:
    """LEFT-side line numbers from an incremental diff are relative to the
    last-reviewed head, not the PR base — posting them would mis-anchor."""
    left = ReviewFinding(
        path="src/app.py", line=1, side="LEFT", severity=Severity.high, title="l", body="b"
    )
    right = ReviewFinding(
        path="src/app.py", line=3, side="RIGHT", severity=Severity.high, title="r", body="b"
    )
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)
    engine = RecordingEngine(findings=[left, right])

    findings, _summary = run_review(
        github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=False
    )

    assert [f.side for f in findings] == ["RIGHT"]


def test_full_review_still_marks_the_watermark() -> None:
    """Even a full review records the reviewed head SHA, so the NEXT run can be
    incremental."""
    github = IncrementalFakeGitHub(CTX, last_sha=None)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=False)

    assert github.marked_reviewed == ["head2222"]


def test_dry_run_never_marks_or_scopes() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=_cfg(incremental=True), dry_run=True)

    assert github.marked_reviewed == []
    assert github.posted == []


# ---------------------------------------------------------------------------
# auto-resolution: incremental=None means "on for a synchronize push"
# ---------------------------------------------------------------------------


def test_auto_resolves_on_for_synchronize_event() -> None:
    cfg = _cfg()  # incremental defaults to None (auto)
    resolved = resolve_auto_incremental(cfg, event_action="synchronize")
    assert resolved.incremental is True


def test_auto_resolves_off_for_opened_event() -> None:
    resolved = resolve_auto_incremental(_cfg(), event_action="opened")
    assert resolved.incremental is False


def test_explicit_config_wins_over_auto() -> None:
    on = resolve_auto_incremental(_cfg(incremental=True), event_action="opened")
    assert on.incremental is True
    off = resolve_auto_incremental(_cfg(incremental=False), event_action="synchronize")
    assert off.incremental is False


# ---------------------------------------------------------------------------
# auto-describe (F3): opt-in structured description on PR open
# ---------------------------------------------------------------------------


def test_auto_describe_only_on_open_events_when_enabled() -> None:
    from lgtmaybe.cli import should_auto_describe

    on = _cfg(auto_describe=True)
    assert should_auto_describe(on, event_action="opened") is True
    assert should_auto_describe(on, event_action="reopened") is True
    assert should_auto_describe(on, event_action="synchronize") is False
    assert should_auto_describe(_cfg(), event_action="opened") is False  # default off


def test_run_describe_posts_via_the_idempotent_upsert() -> None:
    import json as _json

    from lgtmaybe.cli import run_describe
    from lgtmaybe.core.models import ProviderResult
    from tests.fakes import FakeGitHub, FakeProvider

    github = FakeGitHub()
    structured = _json.dumps({"title": "Add a thing", "summary": "Adds it."})
    provider = FakeProvider(result=ProviderResult(text=structured, input_tokens=1, output_tokens=1))

    run_describe(github, provider, _cfg())

    assert len(github.described) == 1
    assert github.described[0].startswith("## Add a thing")
    assert github.comments == []
