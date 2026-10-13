"""The shared "already posted?" match every forge adapter runs before posting."""

from __future__ import annotations

from lgtmaybe.core.comment import unposted
from lgtmaybe.core.findings import finding_fingerprint, finding_identity
from lgtmaybe.core.models import ReviewFinding, Severity


def _finding(title: str, line: int = 2) -> ReviewFinding:
    return ReviewFinding(
        path="a.py",
        line=line,
        side="RIGHT",
        severity=Severity.medium,
        title=title,
        body="b",
        anchor="return None",
        category="correctness",
    )


def test_a_reworded_finding_still_matches_on_its_identity() -> None:
    posted = [{finding_identity(_finding("old wording"))}]
    assert unposted([("c", _finding("new wording"))], posted) == []


def test_each_posted_comment_absorbs_one_candidate() -> None:
    """Two identical flagged lines share one identity: the second is still new."""
    first, second = _finding("t", line=2), _finding("t", line=5)
    posted = [{finding_fingerprint("a.py", "t"), finding_identity(first)}]

    assert unposted([("one", first), ("two", second)], posted) == [("two", second)]


def test_nothing_posted_keeps_every_candidate_in_order() -> None:
    candidates = [("one", _finding("x")), ("two", _finding("y"))]
    assert unposted(candidates, []) == candidates
