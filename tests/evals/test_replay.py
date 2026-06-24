"""Deterministic replay — the GATED Tier-2 guard for the false-positive defenses.

For each replay case we drive the REAL post-parse pipeline, in production order,
against a *recorded* model output + a *recorded* auditor verdict — no live model:

    parse_findings(raw)
      → engine._snap_findings(findings, diff)
      → engine._dedupe(findings)
      → reflect_findings(findings, ctx, cfg, FakeProvider(auditor_verdict))
      → filter on engine._passes_severity_floor

and assert that every recorded false positive is GONE from the survivors while
every genuine catch REMAINS. This pins the behavior the live FP fixtures measure
(but can't gate, needing a model) into the pytest gate: a regression in snapping,
dedupe, reflection, or the severity floor that lets an FP through fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
)
from lgtmaybe.engine import engine as engine_mod
from lgtmaybe.engine.parse import parse_findings
from lgtmaybe.engine.reflect import reflect_findings
from tests.fakes import FakeProvider

_REPLAY = Path(__file__).resolve().parents[2] / "evals" / "replay"
_CASES = ["lazy-imports", "split-hunks", "cloud-semantics", "test-harness"]


def _load(case: str) -> tuple[str, str, str, list[dict]]:
    d = _REPLAY / case
    diff = (d / "diff.txt").read_text()
    raw = (d / "raw_findings.json").read_text()
    verdict = (d / "auditor_verdict.json").read_text()
    survivors = json.loads((d / "expected_survivors.json").read_text())
    return diff, raw, verdict, survivors


def _run_pipeline(diff: str, raw: str, verdict_text: str):
    """Drive the real post-parse pipeline and return the surviving findings."""
    ctx = PRContext(
        diff=diff,
        changed_files=["x"],
        base_sha="0",
        head_sha="1",
        repo="eval/eval",
        pr_number=0,
    )
    cfg = ReviewConfig(provider=Provider.ollama, model="m")

    findings = parse_findings(raw)
    findings = engine_mod._snap_findings(findings, diff)
    findings = engine_mod._dedupe(findings)
    auditor = FakeProvider(
        result=ProviderResult(text=verdict_text, input_tokens=1, output_tokens=1)
    )
    findings = reflect_findings(findings, ctx, cfg, auditor)
    return [f for f in findings if engine_mod._passes_severity_floor(f, cfg)]


def test_replay_cases_all_load() -> None:
    """Every replay case loads its four artifacts and the raw findings parse."""
    for case in _CASES:
        diff, raw, verdict, survivors = _load(case)
        assert diff.strip()
        assert json.loads(raw)["findings"], f"{case}: raw_findings has no findings"
        assert json.loads(verdict)["verdicts"], f"{case}: verdict has no verdicts"
        assert survivors, f"{case}: expected_survivors is empty"


@pytest.mark.parametrize("case", _CASES)
def test_replay_drops_false_positives_keeps_genuine(case: str) -> None:
    diff, raw, verdict_text, expected = _load(case)
    survivors = _run_pipeline(diff, raw, verdict_text)
    survivor_titles = {f.title for f in survivors}

    raw_findings = json.loads(raw)["findings"]
    expected_titles = {e["title"] for e in expected}
    fp_titles = {f["title"] for f in raw_findings if f["title"] not in expected_titles}

    # Every genuine catch survived.
    for e in expected:
        assert e["title"] in survivor_titles, f"{case}: genuine catch {e['title']!r} was dropped"

    # Every recorded false positive is gone.
    for fp in fp_titles:
        assert fp not in survivor_titles, f"{case}: false positive {fp!r} survived"
