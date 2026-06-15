"""Unit tests for the RLM spike's pure plumbing (no model, no I/O)."""

from __future__ import annotations

from typing import Any

from evals.rlm_spike import (
    ComparisonResult,
    StrategyResult,
    _UsageTrackingProvider,
    split_into_hunks,
)
from evals.scorer import FixtureScore
from lgtmaybe.core.models import ProviderResult
from lgtmaybe.core.ports import Message, ProviderClient

# A two-file diff: file A has two hunks, file B has one.
_DIFF = (
    "diff --git a/a.py b/a.py\n"
    "--- a/a.py\n"
    "+++ b/a.py\n"
    "@@ -1,2 +1,3 @@\n"
    " x\n"
    "+added_one\n"
    " y\n"
    "@@ -10,1 +11,2 @@\n"
    " z\n"
    "+added_two\n"
    "diff --git a/b.py b/b.py\n"
    "--- a/b.py\n"
    "+++ b/b.py\n"
    "@@ -1,1 +1,2 @@\n"
    " q\n"
    "+added_three\n"
)


def test_split_into_hunks_yields_one_unit_per_hunk() -> None:
    units = split_into_hunks(_DIFF, ["a.py", "b.py"])
    assert len(units) == 3
    # Exactly one hunk header per unit.
    assert all(u.count("@@ -") == 1 for u in units)


def test_each_hunk_unit_is_a_standalone_diff_with_its_file_header() -> None:
    units = split_into_hunks(_DIFF, ["a.py", "b.py"])
    # The two a.py hunks each carry a.py's header; b.py's hunk carries b.py's.
    a_units = [u for u in units if "added_one" in u or "added_two" in u]
    assert all("+++ b/a.py" in u for u in a_units)
    b_unit = next(u for u in units if "added_three" in u)
    assert "+++ b/b.py" in b_unit
    # A hunk unit does not bleed another hunk's added lines.
    one = next(u for u in units if "added_one" in u)
    assert "added_two" not in one and "added_three" not in one


def test_split_into_hunks_passthrough_when_no_hunk() -> None:
    rename_only = "diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py\n"
    assert split_into_hunks(rename_only, ["new.py"]) == [rename_only]


class _Fake(ProviderClient):
    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        return ProviderResult(text="{}", input_tokens=7, output_tokens=3)


def test_usage_tracking_provider_accumulates_tokens_and_calls() -> None:
    tracker = _UsageTrackingProvider(_Fake())
    tracker.complete([], "m")
    tracker.complete([], "m")
    assert tracker.calls == 2
    assert tracker.input_tokens == 14
    assert tracker.output_tokens == 6


def _score(recall_fraction: tuple[int, int], parsed: bool = True) -> FixtureScore:
    matched, expected = recall_fraction
    return FixtureScore(
        name="f",
        parsed_ok=parsed,
        expected_count=expected,
        matched_count=matched,
        findings_count=matched,
        missed=[],
    )


def _strategy(name: str, recall: tuple[int, int], tokens: int) -> StrategyResult:
    return StrategyResult(
        name=name, score=_score(recall), input_tokens=tokens, output_tokens=0, calls=1
    )


def test_comparison_metrics() -> None:
    cmp = ComparisonResult(
        truncated=_strategy("truncated", (1, 4), tokens=100),
        recursive=_strategy("recursive", (3, 4), tokens=150),
    )
    assert cmp.recall_delta == 0.5  # 75% - 25%
    assert cmp.token_ratio == 1.5
    assert "recall +50%" in cmp.verdict


def test_comparison_verdict_when_recursive_wins_outright() -> None:
    cmp = ComparisonResult(
        truncated=_strategy("truncated", (1, 4), tokens=200),
        recursive=_strategy("recursive", (4, 4), tokens=120),
    )
    assert "recursive wins" in cmp.verdict


def test_comparison_verdict_when_truncated_holds() -> None:
    cmp = ComparisonResult(
        truncated=_strategy("truncated", (3, 4), tokens=100),
        recursive=_strategy("recursive", (3, 4), tokens=180),
    )
    assert "truncated holds" in cmp.verdict
