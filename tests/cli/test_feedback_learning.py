"""Feedback learning wiring in run_review.

When an authorised reviewer reacts 👎 to one of our inline finding comments, the
matching finding is suppressed on the next run. ``run_review`` reads the
downvoted fingerprints from the gateway (best-effort) and attaches them to
``ctx.feedback_downvotes`` before the engine runs, so the suppression pass drops
them before reflection and posting — except a high/critical security finding,
which a downvote can never hide. The 👎 reaction is the ONLY learning signal;
there is no new persistence — the reactions live on GitHub and are re-read each
run.
"""

from __future__ import annotations

from lgtmaybe.cli import run_review
from lgtmaybe.core.models import (
    PRContext,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.engine.suppress import apply_suppressions
from lgtmaybe.github.rest_gateway import finding_fingerprint
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

FINDING = ReviewFinding(
    path="src/app.py",
    line=2,
    side="RIGHT",
    severity=Severity.medium,
    title="Nit worth ignoring",
    body="a human downvoted this",
)
FINDING_FP = finding_fingerprint(FINDING.path, FINDING.title)


class SuppressingEngine:
    """Returns canned findings after applying suppression like the real engine.

    Records the context it was handed so a test can assert the downvoted
    fingerprints arrived on ``ctx.feedback_downvotes`` before review.
    """

    def __init__(self, findings: list[ReviewFinding]) -> None:
        self.findings = findings
        self.reviewed_ctxs: list[PRContext] = []

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        self.reviewed_ctxs.append(ctx)
        kept = apply_suppressions(list(self.findings), cfg, {}, ctx.feedback_downvotes)
        return kept, "summary"


class FeedbackFakeGitHub(FakeGitHub):
    """FakeGitHub that reports 👎-downvoted fingerprints (or raises)."""

    def __init__(
        self,
        ctx: PRContext | None = None,
        *,
        downvoted: set[str] | None = None,
        error: bool = False,
    ) -> None:
        super().__init__(ctx)
        self._downvoted = downvoted or set()
        self._error = error
        self.downvote_calls = 0

    def list_downvoted_fingerprints(self) -> set[str]:
        self.downvote_calls += 1
        if self._error:
            raise RuntimeError("boom")
        return set(self._downvoted)


def test_downvoted_finding_is_suppressed_before_reflection() -> None:
    github = FeedbackFakeGitHub(CTX, downvoted={FINDING_FP})
    engine = SuppressingEngine([FINDING])

    findings, _summary = run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)

    # The downvoted fingerprint arrived on the context the engine saw...
    assert FINDING_FP in engine.reviewed_ctxs[0].feedback_downvotes
    # ...and the matching finding was dropped.
    assert findings == []


SECURITY_FINDING = ReviewFinding(
    path="src/app.py",
    line=2,
    side="RIGHT",
    severity=Severity.high,
    title="SQL injection via string interpolation",
    body="unsanitised input reaches the query",
    category="security",
)
SECURITY_FP = finding_fingerprint(SECURITY_FINDING.path, SECURITY_FINDING.title)


def test_downvoted_high_severity_security_finding_is_not_suppressed() -> None:
    """A 👎 can never hide a high/critical security finding (public-repo safety)."""
    github = FeedbackFakeGitHub(CTX, downvoted={SECURITY_FP})
    engine = SuppressingEngine([SECURITY_FINDING])

    findings, _summary = run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)

    assert findings == [SECURITY_FINDING]  # survived the downvote


def test_learn_feedback_false_skips_the_step() -> None:
    github = FeedbackFakeGitHub(CTX, downvoted={FINDING_FP})
    engine = SuppressingEngine([FINDING])

    findings, _summary = run_review(
        github=github, engine=engine, cfg=make_cfg(learn_feedback=False), dry_run=False
    )

    assert github.downvote_calls == 0  # gateway never queried
    assert findings == [FINDING]  # finding survives


def test_gateway_error_is_swallowed() -> None:
    """A failure reading reactions must never fail the review."""
    github = FeedbackFakeGitHub(CTX, error=True)
    engine = SuppressingEngine([FINDING])

    findings, _summary = run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)

    assert github.downvote_calls == 1
    assert findings == [FINDING]  # review still ran, nothing suppressed


def test_gateway_without_the_method_is_a_no_op() -> None:
    github = FakeGitHub(CTX)  # plain gateway — no list_downvoted_fingerprints
    engine = SuppressingEngine([FINDING])

    findings, _summary = run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)

    assert findings == [FINDING]
