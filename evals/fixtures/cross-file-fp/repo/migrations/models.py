"""Submittal-set models the backfill copies between.

The migration does ``SavedSubmittalSetV2(**row.model_dump())``. A reviewer seeing
only the diff may claim ``model_dump()`` could pass a field absent from V2 — but V2
shares ``_SubmittalBase`` with the source ``Row``, so the field sets are identical
and the construction is total. This is the unshown file that refutes that trap;
symbol resolution fetches it when the auditor defers on ``SavedSubmittalSetV2``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class _SubmittalBase:
    """Every field both the source row and the V2 target carry."""

    id: str
    tenant_id: str  # always populated; pending() filters out any row lacking it
    api_token: str
    payload: dict[str, Any]


@dataclass
class Row(_SubmittalBase):
    """A pending source row. ``model_dump()`` returns exactly the base fields."""

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SavedSubmittalSetV2(_SubmittalBase):
    """The V2 target shape — same fields as the base, so a Row's ``model_dump()``
    maps onto it one-to-one with nothing absent or extra."""
