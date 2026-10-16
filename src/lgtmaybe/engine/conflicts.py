"""Arbitrate opposing advice: two lenses never post contradictory findings.

Every lens is prompted on its own, and no lens prompt names another — so nothing
upstream stops the lean lens saying "cut this" about the very lines the security
lens says need guarding. Posting both tells the author two opposite things and
spends trust neither finding can earn back.

Reflection audits every merged finding in one call and is told to arbitrate
(``reflect.py``); this module is the deterministic referee behind it, so the
guarantee also holds under ``--no-reflect`` and can be tested without a model.

The precedence ladder is Ponytail 5's own review order — correctness, then
safety, then load, then tests, then readability, then leanness — so the lens
that cuts code always yields to the one that fixes it. Only pairs declared in
:data:`OPPOSED` are ever arbitrated: a group id, an unattributed finding, a
custom or pack lens, or a scanner hit declares no opposition, and the backstop
never guesses one.
"""

from __future__ import annotations

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import ReviewCategory, ReviewFinding

_log = get_logger(__name__)

# Highest first. Rendered into the reflection prompt too, so the model referee
# and this one rank the same way.
PRECEDENCE: tuple[ReviewCategory, ...] = (
    ReviewCategory.correctness,
    ReviewCategory.security,
    ReviewCategory.performance,
    ReviewCategory.tests,
    ReviewCategory.complexity,
    ReviewCategory.ponytail,
)

# Lens pairs whose findings on the same code are opposite advice. A cut on lines
# another lens is fixing is moot at best, and at worst removes the guard.
OPPOSED: frozenset[frozenset[ReviewCategory]] = frozenset(
    {
        frozenset({ReviewCategory.ponytail, ReviewCategory.correctness}),
        frozenset({ReviewCategory.ponytail, ReviewCategory.security}),
    }
)

# How close two findings must sit to be about "the same code". A finding anchors
# one line, but its advice covers the statement around it.
NEAR_LINES = 3

_RANK = {category.value: rank for rank, category in enumerate(PRECEDENCE)}
_OPPOSED = frozenset(frozenset(c.value for c in pair) for pair in OPPOSED)


def _outranked_by(loser: ReviewFinding, winner: ReviewFinding) -> bool:
    if loser.path != winner.path or loser.side != winner.side:
        return False
    if abs(loser.line - winner.line) > NEAR_LINES:
        return False
    pair = frozenset({loser.category, winner.category})
    if pair not in _OPPOSED:
        return False
    # Both are ranked: every opposed member is in PRECEDENCE.
    return _RANK[str(winner.category)] < _RANK[str(loser.category)]


def resolve_conflicts(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Drop each finding an opposed, higher-precedence finding sits beside.

    Order is preserved. Judged against the input set, so the result never
    depends on which finding came first.
    """
    kept = [f for f in findings if not any(_outranked_by(f, other) for other in findings)]
    dropped = len(findings) - len(kept)
    if dropped:
        _log.info("dropped opposing advice", extra={"dropped": dropped})
    return kept
