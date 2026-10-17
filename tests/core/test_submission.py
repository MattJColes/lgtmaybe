"""``ReviewSubmission``: everything ``post_review`` acts on, in one value (#833)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lgtmaybe.core import ports
from lgtmaybe.core.models import ReviewFinding, ReviewSubmission, Severity

FINDING = ReviewFinding(
    path="a.py", line=1, side="RIGHT", severity=Severity.low, title="t", body="b"
)


def test_a_bare_submission_completes_nothing() -> None:
    """The safe default is the failure notice: no watermark, no scope, no allowlist."""
    submission = ReviewSubmission(summary="review failed")
    assert submission.findings == []
    assert submission.diff is None
    assert submission.completed_sha is None
    assert submission.diff_fingerprint is None
    assert submission.incremental_scope is None
    assert submission.validated_fixed_threads is None


def test_it_is_frozen() -> None:
    submission = ReviewSubmission(findings=[FINDING], summary="1 finding")
    with pytest.raises(ValidationError):
        submission.completed_sha = "head"  # type: ignore[misc]


def test_it_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewSubmission(summary="s", reviewed_sha="head")  # type: ignore[call-arg]


def test_a_diff_fingerprint_needs_a_completed_head() -> None:
    """The fingerprint describes the completed head; alone it would vouch for nothing."""
    with pytest.raises(ValidationError, match="completed"):
        ReviewSubmission(summary="s", diff_fingerprint="abc")
    assert ReviewSubmission(summary="s", completed_sha="h", diff_fingerprint="abc")


def test_the_port_takes_one_submission() -> None:
    """Posting state rides the call, so the old pre-post setters are gone."""
    assert not hasattr(ports, "SupportsCompletionMark")
    assert not hasattr(ports.SupportsIncremental, "set_incremental_scope")
    assert not hasattr(ports.SupportsDiffFingerprint, "mark_reviewed_diff")
    assert not hasattr(ports.SupportsThreadResolution, "set_validated_fixed_threads")
    # The read-side queries stay: they answer questions, they set nothing.
    assert hasattr(ports.SupportsIncremental, "last_reviewed_sha")
    assert hasattr(ports.SupportsDiffFingerprint, "last_reviewed_diff")
    assert hasattr(ports.SupportsThreadResolution, "list_active_findings")
