"""Persist an eval run's pooled metrics to ``evals/results/<sha>.json``.

A small, pure record so a run's headline numbers (recall / precision / anchored,
the model + settings that produced them, and the per-fixture detail) can be kept
over time and diffed across commits. No model, no git here — the caller supplies
the sha and date — so the round-trip is unit-tested.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .scorer import FixtureScore


class RunRecord(BaseModel):
    """One eval run's headline metrics plus the per-fixture scores that produced them."""

    sha: str
    model: str
    provider: str
    date: str  # ISO date (YYYY-MM-DD); supplied by the caller, never hardcoded
    min_recall: float
    pooled_recall: float
    pooled_precision: float
    pooled_anchored: float
    fixtures: list[FixtureScore]


def write_run_record(record: RunRecord, results_dir: Path) -> Path:
    """Write *record* to ``<results_dir>/<sha>.json`` and return the path.

    Creates *results_dir* if needed. The filename is the run's sha so a later run
    on the same commit overwrites it (the metrics are a property of the commit, not
    of when it was measured).
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{record.sha}.json"
    path.write_text(record.model_dump_json(indent=2) + "\n")
    return path
