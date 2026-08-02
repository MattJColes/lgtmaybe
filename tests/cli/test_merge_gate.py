"""Merge-gate via a Check Run (fail_on) — wiring in run_review.

With ``cfg.fail_on`` set, after posting the review ``run_review`` creates a
GitHub Check Run whose conclusion is ``failure`` when any surviving finding is
at or above the threshold, else ``success``. Off by default (fail_on=None): no
check run is created. Enforcement rides the check run, never PR approval state.
"""

from __future__ import annotations

from lgtmaybe.cli import run_review
from lgtmaybe.core.models import (
    PRContext,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.core.ports import GitHubGateway, ReviewEngine
from tests.conftest import make_cfg
from tests.fakes import FakeGitHub

CTX = PRContext(
    diff="diff --git a/src/app.py b/src/app.py\n@@ -1 +1,2 @@\n old\n+new\n",
    changed_files=["src/app.py"],
    base_sha="base0000",
    head_sha="head2222",
    repo="org/repo",
    pr_number=5,
)


def _finding(severity: Severity) -> ReviewFinding:
    return ReviewFinding(
        path="src/app.py", line=2, side="RIGHT", severity=severity, title="t", body="b"
    )


class CannedEngine(ReviewEngine):
    def __init__(self, findings: list[ReviewFinding]) -> None:
        self._findings = findings

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        return list(self._findings), "summary"


def test_check_run_fails_when_a_finding_meets_the_threshold() -> None:
    github = FakeGitHub(CTX)
    engine = CannedEngine([_finding(Severity.high), _finding(Severity.low)])

    run_review(github=github, engine=engine, cfg=make_cfg(fail_on=Severity.high), dry_run=False)

    assert len(github.check_runs) == 1
    run = github.check_runs[0]
    assert run["conclusion"] == "failure"
    assert run["head_sha"] == CTX.head_sha


def test_check_run_succeeds_when_all_findings_are_below_the_threshold() -> None:
    github = FakeGitHub(CTX)
    engine = CannedEngine([_finding(Severity.low), _finding(Severity.medium)])

    run_review(github=github, engine=engine, cfg=make_cfg(fail_on=Severity.high), dry_run=False)

    assert len(github.check_runs) == 1
    assert github.check_runs[0]["conclusion"] == "success"


def test_check_run_succeeds_on_a_clean_review() -> None:
    github = FakeGitHub(CTX)
    engine = CannedEngine([])

    run_review(github=github, engine=engine, cfg=make_cfg(fail_on=Severity.high), dry_run=False)

    assert github.check_runs[0]["conclusion"] == "success"


def test_no_check_run_when_fail_on_is_none() -> None:
    github = FakeGitHub(CTX)
    engine = CannedEngine([_finding(Severity.critical)])

    # fail_on defaults to None
    run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)

    assert github.check_runs == []


def test_no_check_run_on_dry_run() -> None:
    github = FakeGitHub(CTX)
    engine = CannedEngine([_finding(Severity.critical)])

    run_review(github=github, engine=engine, cfg=make_cfg(fail_on=Severity.high), dry_run=True)

    assert github.check_runs == []


class _PortOnlyGateway(GitHubGateway):
    """A gateway implementing only the frozen port — no create_check_run."""

    def __init__(self, ctx: PRContext) -> None:
        self._ctx = ctx
        self.posted: list[tuple[list[ReviewFinding], str]] = []

    def get_pr_context(self) -> PRContext:
        return self._ctx

    def post_review(self, findings, summary, diff=None) -> None:
        self.posted.append((findings, summary))

    def post_issue_comment(self, body: str) -> None:  # pragma: no cover - unused
        pass


def test_check_run_skipped_on_gateway_without_the_method() -> None:
    """A gateway lacking create_check_run must not crash the review."""
    github = _PortOnlyGateway(CTX)
    engine = CannedEngine([_finding(Severity.critical)])

    run_review(github=github, engine=engine, cfg=make_cfg(fail_on=Severity.high), dry_run=False)

    assert len(github.posted) == 1
