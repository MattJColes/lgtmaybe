"""Unit tests for the RLM benchmark's pure plumbing (no model, no I/O).

The hunk-splitting the benchmark relies on now lives in the engine
(``compress.split_patch_into_hunks``, covered in ``tests/engine/test_compress.py``);
these tests cover the benchmark's own accounting + comparison record.
"""

from __future__ import annotations

from typing import Any

from evals.rlm import (
    ComparisonResult,
    StrategyResult,
    _UsageTrackingProvider,
)
from evals.scorer import FixtureScore
from lgtmaybe.core.models import ProviderResult
from lgtmaybe.core.ports import Message, ProviderClient


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
        whole=_strategy("whole", (1, 4), tokens=100),
        recursive=_strategy("recursive", (3, 4), tokens=150),
    )
    assert cmp.recall_delta == 0.5  # 75% - 25%
    assert cmp.token_ratio == 1.5
    assert "recall +50%" in cmp.verdict


def test_comparison_verdict_when_recursive_wins_outright() -> None:
    cmp = ComparisonResult(
        whole=_strategy("whole", (1, 4), tokens=200),
        recursive=_strategy("recursive", (4, 4), tokens=120),
    )
    assert "recursive wins" in cmp.verdict


def test_comparison_verdict_when_whole_holds() -> None:
    cmp = ComparisonResult(
        whole=_strategy("whole", (3, 4), tokens=100),
        recursive=_strategy("recursive", (3, 4), tokens=180),
    )
    assert "whole-file holds" in cmp.verdict
