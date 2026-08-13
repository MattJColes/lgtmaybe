"""Unit tests for the RLM benchmark's pure aggregation (no model, no I/O).

The hunk-splitting the benchmark relies on lives in the engine
(``compress.split_patch_into_hunks``, covered in ``tests/engine/test_compress.py``);
these cover the benchmark's own accounting, the recall spread across repeats, and
the verdict.
"""

from __future__ import annotations

from typing import Any

from evals.rlm import RunSample, StrategyReport, _UsageTrackingProvider, verdict
from lgtmaybe.core.models import ProviderResult
from lgtmaybe.core.ports import Message


class _Fake:
    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        return ProviderResult(text="{}", input_tokens=7, output_tokens=3)


def test_usage_tracking_provider_accumulates_tokens_and_calls() -> None:
    tracker = _UsageTrackingProvider(_Fake())
    tracker.complete([], "m")
    tracker.complete([], "m")
    assert tracker.calls == 2
    assert tracker.input_tokens == 14
    assert tracker.output_tokens == 6


def _sample(recall: float, tokens: int = 100, parsed_ok: bool = True) -> RunSample:
    return RunSample(
        recall=recall, input_tokens=tokens, output_tokens=0, calls=1, parsed_ok=parsed_ok
    )


def test_report_aggregates_recall_spread_across_repeats() -> None:
    report = StrategyReport(name="recursive", samples=[_sample(0.5), _sample(1.0), _sample(0.75)])
    assert report.mean_recall == 0.75
    recalls = report.recalls
    assert min(recalls) == 0.5
    assert max(recalls) == 1.0
    assert max(recalls) - min(recalls) == 0.5  # max − min: how noisy the result is
    assert report.all_parsed


def test_report_flags_a_parse_failure() -> None:
    report = StrategyReport(name="whole", samples=[_sample(0.6), _sample(0.0, parsed_ok=False)])
    assert not report.all_parsed


def test_verdict_recursive_wins_on_higher_mean_recall() -> None:
    whole = StrategyReport(name="whole", samples=[_sample(0.5, tokens=200)])
    recursive = StrategyReport(name="recursive", samples=[_sample(0.9, tokens=200)])
    assert "recursive recall +40%" in verdict(whole, recursive)


def test_verdict_recursive_wins_outright_when_also_cheaper() -> None:
    whole = StrategyReport(name="whole", samples=[_sample(0.5, tokens=300)])
    recursive = StrategyReport(name="recursive", samples=[_sample(0.9, tokens=120)])
    assert "recursive wins" in verdict(whole, recursive)


def test_verdict_whole_holds_when_recursive_costs_more_for_nothing() -> None:
    whole = StrategyReport(name="whole", samples=[_sample(0.75, tokens=100)])
    recursive = StrategyReport(name="recursive", samples=[_sample(0.75, tokens=180)])
    assert "whole-file holds" in verdict(whole, recursive)
