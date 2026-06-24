"""Round-trip test for the eval results record (pure — no model, no git)."""

from __future__ import annotations

from pathlib import Path

from evals.persist import RunRecord, write_run_record
from evals.scorer import FixtureScore


def _record(sha: str) -> RunRecord:
    return RunRecord(
        sha=sha,
        model="qwen3:4b",
        provider="ollama",
        date="2026-06-23",
        min_recall=0.6,
        pooled_recall=0.72,
        pooled_precision=0.88,
        pooled_anchored=0.95,
        fixtures=[
            FixtureScore(
                name="badcode",
                parsed_ok=True,
                expected_count=7,
                matched_count=5,
                findings_count=6,
                missed=["off-by-one"],
                adjudicable_count=6,
                forbidden_count=0,
                unexpected_count=1,
                anchored_count=6,
            )
        ],
    )


def test_write_run_record_filename_is_sha_json(tmp_path: Path) -> None:
    path = write_run_record(_record("abc123"), tmp_path)
    assert path == tmp_path / "abc123.json"
    assert path.exists()


def test_run_record_round_trips_through_json(tmp_path: Path) -> None:
    original = _record("deadbeef")
    path = write_run_record(original, tmp_path)
    loaded = RunRecord.model_validate_json(path.read_text())
    assert loaded == original
    assert loaded.pooled_precision == 0.88
    assert loaded.fixtures[0].name == "badcode"
    assert loaded.fixtures[0].precision == 1 - 1 / 6  # property survives the round-trip
