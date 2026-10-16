"""Two lenses must never post opposite advice about the same code.

Each lens is prompted on its own, so nothing in a lens prompt can stop ponytail
saying "cut this" about a line the security lens says needs guarding. Reflection
is the first referee; this module is the deterministic one, so the guarantee
holds under ``--no-reflect`` too and is checkable without a model.
"""

from __future__ import annotations

import pytest

from lgtmaybe.core.models import ReviewCategory, ReviewFinding, Severity
from lgtmaybe.engine.conflicts import NEAR_LINES, OPPOSED, PRECEDENCE, resolve_conflicts


def _finding(
    category: str | None,
    line: int = 10,
    *,
    path: str = "a.py",
    side: str = "RIGHT",
    severity: Severity = Severity.low,
) -> ReviewFinding:
    return ReviewFinding(
        path=path,
        line=line,
        side=side,
        severity=severity,
        title=f"{category} finding",
        body="b",
        category=category,
    )


def test_precedence_puts_leanness_last() -> None:
    """Ponytail 5's own review order: correctness and safety first, leanness last."""
    assert PRECEDENCE[0] == ReviewCategory.correctness
    assert PRECEDENCE[-1] == ReviewCategory.ponytail


def test_every_opposed_pair_is_ranked() -> None:
    """An opposed pair with an unranked member could never be arbitrated."""
    for pair in OPPOSED:
        assert len(pair) == 2
        assert pair <= set(PRECEDENCE)


@pytest.mark.parametrize("winner", [ReviewCategory.security, ReviewCategory.correctness])
def test_a_cut_next_to_a_fix_loses(winner: ReviewCategory) -> None:
    keep = _finding(winner, line=10)
    cut = _finding(ReviewCategory.ponytail, line=10 + NEAR_LINES)

    assert resolve_conflicts([cut, keep]) == [keep]


def test_the_lower_ranked_side_loses_whatever_its_severity() -> None:
    """Precedence, not grade, decides: a `medium` cut still loses to a `low` fix."""
    keep = _finding(ReviewCategory.security, severity=Severity.low)
    cut = _finding(ReviewCategory.ponytail, severity=Severity.medium)

    assert resolve_conflicts([keep, cut]) == [keep]


def test_findings_outside_the_window_both_post() -> None:
    near = _finding(ReviewCategory.security, line=10)
    far = _finding(ReviewCategory.ponytail, line=10 + NEAR_LINES + 1)

    assert resolve_conflicts([near, far]) == [near, far]


@pytest.mark.parametrize(
    "other",
    [
        _finding(ReviewCategory.ponytail, path="b.py"),
        _finding(ReviewCategory.ponytail, side="LEFT"),
    ],
    ids=["other-file", "other-side"],
)
def test_only_the_same_code_conflicts(other: ReviewFinding) -> None:
    keep = _finding(ReviewCategory.security)

    assert resolve_conflicts([keep, other]) == [keep, other]


def test_pairs_not_declared_opposed_both_post() -> None:
    """Performance and ponytail can both be right about one loop."""
    a = _finding(ReviewCategory.performance)
    b = _finding(ReviewCategory.ponytail)

    assert resolve_conflicts([a, b]) == [a, b]


@pytest.mark.parametrize(
    "category",
    ["code-health", None, "my-custom-lens", "scan:bandit", "wrong-abstraction"],
)
def test_undeclared_categories_are_never_guessed_at(category: str | None) -> None:
    """A group id, an unattributed finding, a custom or pack lens, or a scanner hit
    declares no opposition, so the backstop leaves it alone."""
    a = _finding(ReviewCategory.security)
    b = _finding(category)

    assert resolve_conflicts([a, b]) == [a, b]


def test_order_is_preserved_and_unrelated_findings_untouched() -> None:
    first = _finding(ReviewCategory.tests, line=1)
    keep = _finding(ReviewCategory.correctness, line=40)
    cut = _finding(ReviewCategory.ponytail, line=41)
    last = _finding(ReviewCategory.documentation, line=90)

    assert resolve_conflicts([first, cut, keep, last]) == [first, keep, last]


def test_no_findings() -> None:
    assert resolve_conflicts([]) == []


def test_each_drop_names_the_loser_and_the_winner(caplog: pytest.LogCaptureFixture) -> None:
    """A count can't tell an operator which finding vanished or why; each drop logs
    the losing finding and the lens that outranked it."""
    keep = _finding(ReviewCategory.security, line=10)
    cut = _finding(ReviewCategory.ponytail, line=11)

    with caplog.at_level("INFO", logger="lgtmaybe.engine.conflicts"):
        resolve_conflicts([keep, cut])

    (record,) = [r for r in caplog.records if r.getMessage() == "dropped opposing advice"]
    assert record.path == "a.py"  # type: ignore[attr-defined]
    assert record.line == 11  # type: ignore[attr-defined]
    assert record.category == "ponytail"  # type: ignore[attr-defined]
    assert record.title == cut.title  # type: ignore[attr-defined]
    assert record.outranked_by == "security"  # type: ignore[attr-defined]
    assert record.winner_line == 10  # type: ignore[attr-defined]
