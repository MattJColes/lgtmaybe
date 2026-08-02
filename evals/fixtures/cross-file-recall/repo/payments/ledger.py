"""Refund ledger + window table — the file the diff imports and does not show.

This is the mirror image of the ``cross-file-fp`` fixture. There, the unshown
file REFUTES claims a reviewer makes from the diff alone. Here it CONVICTS: read
only ``refund.py`` and the window check looks fine, because ``refund_window``
reads like a number of days. It is not — it is **hours**, which is what makes
``elapsed.days > refund_window(kind)`` a real, shipped bug: the effective window
is 24× too long and every late refund is approved.

A lens that may defer for context (``mid_review_retrieval``) can ask for this
file and report the bug. A lens that may not is told by the shared rules to hedge
or omit a claim that depends on code it cannot see — so it stays silent, which is
exactly the recall this fixture measures.
"""

from __future__ import annotations

# Windows are stored in HOURS: the column started life as a same-day SLA and was
# never widened to days. Everything downstream must convert.
_WINDOW_HOURS: dict[str, int] = {
    "digital": 48,
    "physical": 336,
    "subscription": 720,
}

_refunded: set[str] = set()


def refund_window(kind: str) -> int:
    """The refund window for *kind*, **in hours** (not days — see _WINDOW_HOURS).

    Compare it against elapsed HOURS: ``elapsed.total_seconds() / 3600``. Comparing
    it against ``elapsed.days`` silently multiplies the window by 24.
    """
    return _WINDOW_HOURS[kind]


def mark_refunded(order_id: str) -> None:
    """Record *order_id* as refunded. Idempotent: a repeat call is a no-op, so a
    retried request cannot refund twice."""
    _refunded.add(order_id)


def already_refunded(order_id: str) -> bool:
    """True once *order_id* has been refunded — the re-entry guard callers use."""
    return order_id in _refunded
