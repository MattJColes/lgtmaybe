"""Orchestration of commit-scoped incremental review (P2) in run_review.

With ``cfg.incremental`` on and a gateway that can supply the last-reviewed
SHA and the compare diff, ``run_review`` reviews only the increment and posts
against the full PR diff. Every degraded case — no marker (first review),
force-push/rebase (compare returns None), same head, a gateway without the
adapter methods — falls back to the full review, byte-for-byte the old
behaviour.
"""

from __future__ import annotations

import pytest

from lgtmaybe.cli import resolve_auto_incremental, run_review
from lgtmaybe.core.models import (
    ActiveFinding,
    PRContext,
    ProviderResult,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from tests.conftest import make_cfg
from tests.fakes import FakeGitHub, FakeProvider

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


class RecordingEngine:
    """Returns canned findings; records the ctx it was asked to review."""

    def __init__(
        self,
        findings: list[ReviewFinding] | None = None,
        summary: str = "1 finding · summary",
    ) -> None:
        self.findings = findings or []
        self.summary = summary
        self.reviewed_ctxs: list[PRContext] = []

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        self.reviewed_ctxs.append(ctx)
        return list(self.findings), self.summary


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
        self.marked_reviewed: list[str | None] = []
        self.scopes: list[set[str] | None] = []
        self.last_completed_calls: list[bool] = []

    def last_completed_sha(self, *, diagram_required: bool) -> str | None:
        self.last_completed_calls.append(diagram_required)
        return self._last_sha

    def compare_diff(self, base_sha: str, head_sha: str) -> str | None:
        self.compare_calls.append((base_sha, head_sha))
        return self._compare_result

    def mark_reviewed(self, head_sha: str | None) -> None:
        self.marked_reviewed.append(head_sha)

    def set_incremental_scope(self, paths: set[str] | None) -> None:
        self.scopes.append(paths)


def test_incremental_reviews_only_the_increment() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)
    engine = RecordingEngine()

    _findings, summary = run_review(
        github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False
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

    run_review(github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False)

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF
    assert github.compare_calls == []
    assert github.scopes == []  # full review: resolve-on-fix unrestricted


def test_force_push_falls_back_to_full_review() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=None)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False)

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF
    assert github.compare_calls == [("head1111", "head2222")]


def test_same_completed_head_is_a_no_op() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head2222", compare_result=INC_DIFF)
    engine = RecordingEngine()

    findings, summary = run_review(
        github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False
    )

    assert findings == []
    assert "already complete" in summary.lower()
    assert engine.reviewed_ctxs == []
    assert github.posted == []
    assert github.compare_calls == []


def test_incremental_off_never_queries_the_marker() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)  # default: auto/off

    assert engine.reviewed_ctxs[0].diff == FULL_DIFF
    assert github.last_completed_calls == []


def test_gateway_without_incremental_methods_reviews_full() -> None:
    github = FakeGitHub(CTX)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False)

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
        github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False
    )

    assert [f.side for f in findings] == ["RIGHT"]


def test_full_review_still_marks_the_watermark() -> None:
    """Even a full review records the reviewed head SHA, so the NEXT run can be
    incremental."""
    github = IncrementalFakeGitHub(CTX, last_sha=None)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False)

    assert github.marked_reviewed == ["head2222"]


def test_incremental_validates_prior_findings_and_allows_only_fixed_resolution() -> None:
    class HybridGitHub(IncrementalFakeGitHub):
        def __init__(self) -> None:
            super().__init__(CTX, last_sha="head1111", compare_result=INC_DIFF)
            self.fixed_thread_ids: list[set[str]] = []

        def list_active_findings(self) -> list[ActiveFinding]:
            return [
                ActiveFinding(thread_id="FIXED", path="src/app.py", body="old bug", outdated=True),
                ActiveFinding(thread_id="OPEN", path="src/other.py", body="still broken"),
            ]

        def set_validated_fixed_threads(self, thread_ids: set[str]) -> None:
            self.fixed_thread_ids.append(thread_ids)

    provider = FakeProvider(
        result=ProviderResult(
            text=(
                '{"verdicts": ['
                '{"thread_id":"FIXED","status":"fixed","reason":"removed"},'
                '{"thread_id":"OPEN","status":"still_open","reason":"remains"}'
                "]}"
            ),
            input_tokens=1,
            output_tokens=1,
        )
    )
    github = HybridGitHub()

    findings, summary = run_review(
        github=github,
        engine=RecordingEngine(),
        cfg=make_cfg(incremental=True),
        dry_run=False,
        provider=provider,
    )

    assert findings == []
    assert github.fixed_thread_ids == [{"FIXED"}]
    assert "1 fixed" in summary
    assert "1 still open" in summary


def test_model_fixed_verdict_needs_an_outdated_thread() -> None:
    class HybridGitHub(IncrementalFakeGitHub):
        def __init__(self) -> None:
            super().__init__(CTX, last_sha="head1111", compare_result=INC_DIFF)
            self.fixed_thread_ids: set[str] | None = None

        def list_active_findings(self) -> list[ActiveFinding]:
            return [ActiveFinding(thread_id="T1", path="src/app.py", body="old bug")]

        def set_validated_fixed_threads(self, thread_ids: set[str]) -> None:
            self.fixed_thread_ids = thread_ids

    provider = FakeProvider(
        result=ProviderResult(
            text='{"verdicts":[{"thread_id":"T1","status":"fixed","reason":"removed"}]}',
            input_tokens=1,
            output_tokens=1,
        )
    )
    github = HybridGitHub()

    _findings, summary = run_review(
        github=github,
        engine=RecordingEngine(),
        cfg=make_cfg(incremental=True),
        dry_run=False,
        provider=provider,
    )

    assert github.fixed_thread_ids == set()
    assert "1 uncertain" in summary


def test_followup_validation_unavailable_resolves_nothing() -> None:
    class HybridGitHub(IncrementalFakeGitHub):
        def __init__(self) -> None:
            super().__init__(CTX, last_sha="head1111", compare_result=INC_DIFF)
            self.fixed_thread_ids: set[str] | None = None

        def list_active_findings(self) -> list[ActiveFinding]:
            raise RuntimeError("GitHub lookup failed")

        def set_validated_fixed_threads(self, thread_ids: set[str]) -> None:
            self.fixed_thread_ids = thread_ids

    github = HybridGitHub()

    _findings, summary = run_review(
        github=github,
        engine=RecordingEngine(),
        cfg=make_cfg(incremental=True),
        dry_run=False,
        provider=FakeProvider(),
    )

    assert github.fixed_thread_ids == set()
    assert "remain open" in summary


def test_full_review_does_not_run_followup_validation() -> None:
    class HybridGitHub(IncrementalFakeGitHub):
        def list_active_findings(self) -> list[ActiveFinding]:
            raise AssertionError("full review must not read active findings")

    github = HybridGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)

    run_review(
        github=github,
        engine=RecordingEngine(),
        cfg=make_cfg(incremental=False),
        dry_run=False,
        provider=FakeProvider(),
    )


def test_incomplete_review_does_not_advance_the_watermark() -> None:
    from lgtmaybe.engine import INCOMPLETE_MARKER

    github = IncrementalFakeGitHub(CTX, last_sha=None)
    engine = RecordingEngine(summary=f"partial\n{INCOMPLETE_MARKER}")

    run_review(github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False)

    assert github.marked_reviewed == [None]
    assert len(github.posted) == 1


def test_dry_run_never_marks_or_scopes() -> None:
    github = IncrementalFakeGitHub(CTX, last_sha="head1111", compare_result=INC_DIFF)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=True)

    assert github.marked_reviewed == []
    assert github.posted == []


# ---------------------------------------------------------------------------
# auto-resolution: incremental=None means "on for a synchronize push"
# ---------------------------------------------------------------------------


def test_auto_resolves_on_for_synchronize_event() -> None:
    cfg = make_cfg()  # incremental defaults to None (auto)
    resolved = resolve_auto_incremental(cfg, event_action="synchronize")
    assert resolved.incremental is True


def test_auto_resolves_off_for_opened_event() -> None:
    resolved = resolve_auto_incremental(make_cfg(), event_action="opened")
    assert resolved.incremental is False


def test_explicit_config_wins_over_auto() -> None:
    on = resolve_auto_incremental(make_cfg(incremental=True), event_action="opened")
    assert on.incremental is True
    off = resolve_auto_incremental(make_cfg(incremental=False), event_action="synchronize")
    assert off.incremental is False


# ---------------------------------------------------------------------------
# the change overview: description + high impact areas + diagrams, one comment
# ---------------------------------------------------------------------------


def test_run_describe_posts_via_the_idempotent_upsert() -> None:
    import json as _json

    from lgtmaybe.cli import run_describe
    from lgtmaybe.core.models import ProviderResult
    from tests.fakes import FakeGitHub, FakeProvider

    github = FakeGitHub()
    structured = _json.dumps({"title": "Add a thing", "summary": "Adds it."})
    provider = FakeProvider(result=ProviderResult(text=structured, input_tokens=1, output_tokens=1))

    run_describe(github, provider, make_cfg())

    assert len(github.described) == 1
    assert github.described[0].startswith("## Add a thing")
    assert github.comments == []


def test_auto_diagram_on_pr_change_events_when_enabled() -> None:
    from lgtmaybe.cli import should_auto_diagram

    on = make_cfg(auto_diagram=True)
    assert should_auto_diagram(on, event_action="opened") is True
    assert should_auto_diagram(on, event_action="reopened") is True
    assert should_auto_diagram(on, event_action="synchronize") is True
    assert should_auto_diagram(on, event_action="closed") is False
    assert should_auto_diagram(make_cfg(), event_action="opened") is True  # default on
    assert should_auto_diagram(make_cfg(auto_diagram=False), event_action="opened") is False
    # The opt-out has to hold on every eligible event, not just the first one.
    # Asserting it only on `opened` lets a mutant that ORs synchronize into the
    # enable condition — the plausible slip when adding a new event — pass the
    # whole suite while ignoring `auto_diagram: false` on a push.
    assert should_auto_diagram(make_cfg(auto_diagram=False), event_action="synchronize") is False
    assert should_auto_diagram(make_cfg(auto_diagram=False), event_action="reopened") is False


def test_run_diagram_posts_via_the_idempotent_upsert() -> None:
    import json as _json

    from lgtmaybe.cli import run_diagram
    from lgtmaybe.core.models import ProviderResult
    from tests.fakes import FakeGitHub, FakeProvider

    github = FakeGitHub()
    structured = _json.dumps(
        {
            "title": "Change map",
            "nodes": [
                {
                    "id": "app",
                    "label": "App",
                    "technology": "",
                    "description": "",
                    "change": "changed",
                }
            ],
            "edges": [],
            "notes": "",
        }
    )
    from lgtmaybe.core.models import DescribeResult, DiagramResult, HighImpactResult

    # Routed by schema: one canned result would feed the diagram payload to the
    # describe call too, whose lenient "has a title" predicate accepts it.
    provider = FakeProvider(
        results_by_schema={
            DiagramResult: ProviderResult(text=structured, input_tokens=1, output_tokens=1),
            DescribeResult: ProviderResult(
                text=_json.dumps({"title": "Add a thing", "summary": "Adds it."}),
                input_tokens=1,
                output_tokens=1,
            ),
            HighImpactResult: ProviderResult(
                text=_json.dumps({"areas": [], "notes": ""}), input_tokens=1, output_tokens=1
            ),
        }
    )

    run_diagram(github, provider, make_cfg())

    assert len(github.diagrams) == 1
    body = github.diagrams[0]
    assert body.startswith("## Add a thing")
    assert "### **High Impact Areas**" in body
    assert "```mermaid" in body
    assert github.comments == []


def test_run_diagram_falls_back_to_issue_comment_without_upsert() -> None:
    """A gateway lacking post_diagram_comment still gets the body via a plain comment."""
    import json as _json

    from lgtmaybe.cli import run_diagram
    from lgtmaybe.core.models import PRContext, ProviderResult

    class PlainGateway:
        def __init__(self) -> None:
            self.comments: list[str] = []

        def get_pr_context(self) -> PRContext:
            return PRContext(
                diff="diff --git a/a b/a\n@@ -1 +1 @@\n-x\n+y\n",
                changed_files=["a"],
                base_sha="b",
                head_sha="h",
                repo="o/r",
                pr_number=1,
            )

        def post_review(self, findings, summary, diff=None) -> None:  # pragma: no cover - unused
            pass

        def post_issue_comment(self, body: str) -> None:
            self.comments.append(body)

    from tests.fakes import FakeProvider

    github = PlainGateway()
    structured = _json.dumps(
        {
            "title": "Change map",
            "nodes": [
                {
                    "id": "app",
                    "label": "App",
                    "technology": "",
                    "description": "",
                    "change": "changed",
                }
            ],
            "edges": [],
            "notes": "",
        }
    )
    provider = FakeProvider(result=ProviderResult(text=structured, input_tokens=1, output_tokens=1))

    run_diagram(github, provider, make_cfg())

    assert len(github.comments) == 1
    assert "```mermaid" in github.comments[0]


def test_required_diagram_fails_without_a_completion_aware_gateway() -> None:
    import json as _json

    from lgtmaybe.cli import run_diagram
    from lgtmaybe.core.models import PRContext, ProviderResult

    class PlainGateway:
        def get_pr_context(self) -> PRContext:
            return PRContext(
                diff="diff --git a/a b/a\n@@ -1 +1 @@\n-x\n+y\n",
                changed_files=["a"],
                base_sha="b",
                head_sha="h",
                repo="o/r",
                pr_number=1,
            )

        def post_review(self, findings, summary, diff=None) -> None:  # pragma: no cover
            pass

        def post_issue_comment(self, body: str) -> None:  # pragma: no cover
            raise AssertionError("a required diagram must not use the generic fallback")

    structured = _json.dumps(
        {
            "title": "Change map",
            "nodes": [],
            "edges": [],
            "notes": "",
        }
    )

    with pytest.raises(RuntimeError, match="completion marker"):
        run_diagram(
            PlainGateway(),
            FakeProvider(result=ProviderResult(text=structured, input_tokens=1, output_tokens=1)),
            make_cfg(),
            completed_sha="h",
        )


def test_manual_diagram_fails_when_the_gateway_cannot_post() -> None:
    import json as _json

    from lgtmaybe.cli import run_diagram
    from lgtmaybe.core.models import PRContext, ProviderResult

    class ReadOnlyGateway:
        def get_pr_context(self) -> PRContext:
            return PRContext(
                diff="diff --git a/a b/a\n@@ -1 +1 @@\n-x\n+y\n",
                changed_files=["a"],
                base_sha="b",
                head_sha="h",
                repo="o/r",
                pr_number=1,
            )

        def post_review(self, findings, summary, diff=None) -> None:  # pragma: no cover
            pass

    structured = _json.dumps({"title": "Change map", "nodes": [], "edges": [], "notes": ""})

    with pytest.raises(RuntimeError, match="cannot post a diagram"):
        run_diagram(
            ReadOnlyGateway(),
            FakeProvider(result=ProviderResult(text=structured, input_tokens=1, output_tokens=1)),
            make_cfg(),
        )


# ---------------------------------------------------------------------------
# PR labels (F4): applied only when opted in, on capable gateways
# ---------------------------------------------------------------------------


class LabelFakeGitHub(FakeGitHub):
    def __init__(self, ctx: PRContext | None = None) -> None:
        super().__init__(ctx)
        self.labels: list[list[str]] = []

    def apply_pr_labels(self, labels: list[str]) -> None:
        self.labels.append(labels)


def test_pr_labels_applied_when_enabled() -> None:
    github = LabelFakeGitHub(CTX)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(pr_labels=True), dry_run=False)

    assert len(github.labels) == 1
    assert any(label.startswith("review-effort/") for label in github.labels[0])


def test_pr_labels_off_by_default() -> None:
    github = LabelFakeGitHub(CTX)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)

    assert github.labels == []


def test_pr_labels_skipped_on_dry_run_and_plain_gateway() -> None:
    github = FakeGitHub(CTX)  # no apply_pr_labels — must not crash
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(pr_labels=True), dry_run=False)
    run_review(github=github, engine=engine, cfg=make_cfg(pr_labels=True), dry_run=True)
