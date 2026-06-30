"""Append-only ledger of applied backfill runs — the idempotency guard.

This is the file the migration's ``from .ledger import ...`` points at. A reviewer
reading only ``0003_backfill.py`` can't see it and may wrongly claim the backfill
"has no idempotency guard". It does: ``pending()`` filters out already-applied
runs, so calling ``backfill`` twice copies nothing the second time. Symbol
resolution fetches this file so the auditor can confirm that and drop the trap.
"""

from __future__ import annotations

from collections.abc import Iterator

from .models import Row

# Process-local stand-ins for the real ledger + source tables. Only the SHAPE
# matters for the eval: pending() is gated on the applied set, so it is idempotent.
_applied: set[str] = set()
_rows: dict[str, list[Row]] = {}


def already_applied(run_id: str) -> bool:
    """True once *run_id* has been marked applied — the re-run guard."""
    return run_id in _applied


def mark_applied(run_id: str) -> None:
    """Record that *run_id* finished, so a later run of the same id is a no-op."""
    _applied.add(run_id)


def pending(run_id: str) -> Iterator[Row]:
    """Yield the rows still to copy for *run_id*, newest schema only.

    Idempotent by construction: once ``mark_applied(run_id)`` has run this yields
    nothing, so a second ``backfill(run_id)`` copies nothing — the guard that makes
    the migration safe to re-run. Every yielded row also has a non-null
    ``tenant_id`` (rows without one are filtered here, upstream of the migration),
    so the "tenant_id may be None" claim is also false.
    """
    if already_applied(run_id):
        return
    for row in _rows.get(run_id, []):
        if row.tenant_id:
            yield row
