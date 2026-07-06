"""Unit tests for the A/B benchmark's pure aggregation (no model, no I/O, no git).

The live A/B runner shells out to a ``git worktree`` at ``--baseline-ref`` and runs
``python -m evals.run --json`` there; these cover only the pure accounting it feeds:
pooling recall/precision over raw counts, the deltas, and the verdict string.
"""

from __future__ import annotations

from evals.ab import ABLeg, ABReport, _pool_legs, ab_verdict
from evals.scorer import FixtureScore


def _score(
    name: str,
    *,
    matched: int,
    expected: int,
    adjudicable: int,
    wrong: int,
) -> FixtureScore:
    return FixtureScore(
        name=name,
        parsed_ok=True,
        expected_count=expected,
        matched_count=matched,
        findings_count=adjudicable,
        missed=[],
        adjudicable_count=adjudicable,
        forbidden_count=wrong,
        unexpected_count=0,
        anchored_count=adjudicable,
    )


def _leg(ref: str, recall: float, precision: float) -> ABLeg:
    return ABLeg(
        ref=ref,
        pooled_recall=recall,
        pooled_precision=precision,
        anchored_rate=1.0,
        per_fixture=[],
    )


def test_pool_legs_pools_over_counts_not_averages_of_percentages() -> None:
    """Pooling weights a fixture by its raw counts, not by giving each fixture an
    equal vote — a 100%-recall 1-finding fixture and a 0%-recall 9-finding one pool
    to 9/10 missed, not 50%."""
    scores = [
        _score("small", matched=1, expected=1, adjudicable=1, wrong=0),  # recall 100%, prec 100%
        _score("big", matched=0, expected=9, adjudicable=9, wrong=9),  # recall 0%, prec 0%
    ]
    leg = _pool_legs("ref", scores)
    assert leg.pooled_recall == 1 / 10  # 1 caught of 10 planted, NOT (100%+0%)/2
    assert leg.pooled_precision == 1 - 9 / 10  # 1 right of 10 adjudicable
    assert leg.ref == "ref"
    assert len(leg.per_fixture) == 2


def test_pool_legs_precision_is_one_when_nothing_adjudicable() -> None:
    scores = [_score("f", matched=1, expected=1, adjudicable=0, wrong=0)]
    leg = _pool_legs("ref", scores)
    assert leg.pooled_precision == 1.0
    assert leg.pooled_recall == 1.0


def test_report_computes_deltas_current_minus_baseline() -> None:
    baseline = _leg("main", recall=0.5, precision=0.9)
    current = _leg("HEAD", recall=0.7, precision=0.6)
    report = ABReport(baseline=baseline, current=current)
    assert abs(report.recall_delta - 0.2) < 1e-9
    assert abs(report.precision_delta - (-0.3)) < 1e-9


def test_verdict_recall_up_precision_down() -> None:
    report = ABReport(
        baseline=_leg("main", 0.5, 0.9),
        current=_leg("HEAD", 0.7, 0.6),
    )
    out = ab_verdict(report)
    assert "recall +20%" in out
    assert "precision -30%" in out


def test_verdict_recall_down_precision_up() -> None:
    report = ABReport(
        baseline=_leg("main", 0.7, 0.6),
        current=_leg("HEAD", 0.5, 0.9),
    )
    out = ab_verdict(report)
    assert "recall -20%" in out
    assert "precision +30%" in out


def test_verdict_no_change_reads_as_flat() -> None:
    report = ABReport(
        baseline=_leg("main", 0.6, 0.6),
        current=_leg("HEAD", 0.6, 0.6),
    )
    out = ab_verdict(report).lower()
    assert "no change" in out or "flat" in out


class TestPresetAxis:
    """The --preset flag: a list sweeps the preset on the current tree; a single
    value pins it (passthrough to both legs of a ref comparison)."""

    def _run(self, monkeypatch, argv: list[str]) -> list[tuple[str, list[str]]]:
        """Run ab.main with legs faked out; returns (label, extra_args) per leg."""
        import evals.ab as ab_mod

        calls: list[tuple[str, list[str]]] = []

        def fake_current_leg(fixtures_dir, *, provider, model, extra_args, label="working-tree"):
            calls.append((label, list(extra_args)))
            return _leg(label, 0.5, 0.9)

        def fake_baseline_leg(ref, fixtures_dir, *, provider, model, extra_args):
            calls.append((ref, list(extra_args)))
            return _leg(ref, 0.5, 0.9)

        monkeypatch.setattr(ab_mod, "_current_leg", fake_current_leg)
        monkeypatch.setattr(ab_mod, "_baseline_leg", fake_baseline_leg)
        assert ab_mod.main(argv) == 0
        return calls

    def test_preset_list_sweeps_on_the_current_tree(self, monkeypatch) -> None:
        calls = self._run(
            monkeypatch,
            ["--provider", "ollama", "--model", "x", "--preset", "full,fast"],
        )
        assert [label for label, _ in calls] == ["preset=full", "preset=fast"]
        assert ["--preset", "full"] == calls[0][1][-2:]
        assert ["--preset", "fast"] == calls[1][1][-2:]

    def test_single_preset_pins_both_legs_of_a_ref_comparison(self, monkeypatch) -> None:
        calls = self._run(
            monkeypatch,
            [
                "--provider",
                "ollama",
                "--model",
                "x",
                "--baseline-ref",
                "v0.10.0",
                "--preset",
                "full",
            ],
        )
        assert len(calls) == 2  # baseline + current
        for _label, extra in calls:
            assert "--preset" in extra and extra[extra.index("--preset") + 1] == "full"

    def test_two_axes_at_once_is_an_error(self, monkeypatch) -> None:
        import pytest as _pytest

        with _pytest.raises(SystemExit):
            self._run(
                monkeypatch,
                [
                    "--provider",
                    "ollama",
                    "--model",
                    "x",
                    "--preset",
                    "full,fast",
                    "--context-lines",
                    "20,0",
                ],
            )
