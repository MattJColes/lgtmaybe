"""Payment-gateway stub — the other file ``refund.py`` imports but never shows.

``capture_refund`` is safe to call twice (the provider de-duplicates on the order
id), so "this refund is not idempotent" is a guess about code the diff does not
show, not a finding. The genuine bug in this fixture lives in ``ledger.py``.
"""

from __future__ import annotations


def capture_refund(order_id: str, amount_cents: int) -> None:
    """Send the refund to the provider. Idempotent per (order_id, amount)."""
