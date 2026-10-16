"""The shared posting decisions every forge adapter runs: split, then "already posted?"."""

from __future__ import annotations

from typing import Any

from lgtmaybe.core.comment import partition_findings, unposted
from lgtmaybe.core.diff import CommentableLines
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


class TestPartitionFindings:
    def _place(self, f: ReviewFinding) -> dict[str, Any]:
        return {"path": f.path, "line": f.line}

    def test_an_anchored_commentable_finding_is_placed_with_its_payload(self) -> None:
        commentable: CommentableLines = {("a.py", 2, "RIGHT")}
        finding = _finding("t")

        placeable, demoted, broad = partition_findings([finding], commentable, self._place)

        assert placeable == [({"path": "a.py", "line": 2}, finding)]
        assert demoted == []
        assert broad == []

    def test_a_broad_finding_is_routed_to_its_own_group(self) -> None:
        commentable: CommentableLines = {("a.py", 2, "RIGHT")}
        finding = _finding("t").model_copy(update={"broad": True})

        placeable, demoted, broad = partition_findings([finding], commentable, self._place)

        assert placeable == []
        assert demoted == []
        assert broad == [finding]

    def test_an_unanchored_or_off_diff_finding_is_demoted(self) -> None:
        unanchored = _finding("t").model_copy(update={"anchored": False})
        off_diff = _finding("t", line=40)

        _placeable, demoted, _broad = partition_findings(
            [unanchored, off_diff], {("a.py", 2, "RIGHT")}, self._place
        )

        assert demoted == [unanchored, off_diff]

    def test_a_host_that_cannot_place_a_finding_demotes_it(self) -> None:
        finding = _finding("t")

        _placeable, demoted, _broad = partition_findings(
            [finding], {("a.py", 2, "RIGHT")}, lambda _f: None
        )

        assert demoted == [finding]
