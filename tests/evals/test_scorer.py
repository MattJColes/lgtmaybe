"""Unit tests for the eval scorer (pure — no model)."""

from __future__ import annotations

from pathlib import Path

from evals.scorer import ExpectedFinding, Fixture, score_fixture
from lgtmaybe.core.models import ReviewFinding, Severity


def _finding(line: int, title: str, body: str = "", severity: Severity = Severity.high):
    return ReviewFinding(path="badcode.py", line=line, severity=severity, title=title, body=body)


def _expected(line: int, keywords: list[str], severity: Severity | None = None):
    return ExpectedFinding(
        label=f"line {line}", line=line, keywords=keywords, severity_at_least=severity
    )


def test_finding_matches_on_line_keyword_and_severity() -> None:
    findings = [_finding(30, "Command injection via shell=True")]
    expected = [_expected(30, ["injection", "shell"], Severity.high)]
    score = score_fixture("f", findings, expected)
    assert score.recall == 1.0
    assert score.missed == []


def test_line_drift_within_tolerance_still_matches() -> None:
    findings = [_finding(31, "shell injection")]  # expected line 30, drift 1 (def vs statement)
    score = score_fixture("f", findings, [_expected(30, ["injection"])])
    assert score.matched_count == 1


def test_line_drift_beyond_one_misses() -> None:
    # Deterministic anchoring lands a real finding on its exact line, so drift > 1
    # means a mis-placed finding — the scorer must not credit it.
    findings = [_finding(32, "shell injection")]  # expected line 30, drift 2
    score = score_fixture("f", findings, [_expected(30, ["injection"])])
    assert score.matched_count == 0


def test_line_too_far_misses() -> None:
    findings = [_finding(40, "shell injection")]  # expected 30, drift 10
    score = score_fixture("f", findings, [_expected(30, ["injection"])])
    assert score.matched_count == 0
    assert score.recall == 0.0


def test_keyword_mismatch_misses() -> None:
    findings = [_finding(30, "style nit")]
    score = score_fixture("f", findings, [_expected(30, ["injection", "shell"])])
    assert score.matched_count == 0


def test_severity_below_floor_misses() -> None:
    findings = [_finding(30, "shell injection", severity=Severity.low)]
    score = score_fixture("f", findings, [_expected(30, ["injection"], Severity.high)])
    assert score.matched_count == 0


def test_keyword_matches_in_body_not_only_title() -> None:
    findings = [_finding(16, "Logic bug", body="classic off-by-one in the range")]
    score = score_fixture("f", findings, [_expected(16, ["off-by-one"])])
    assert score.matched_count == 1


def test_partial_recall_lists_missed_labels() -> None:
    findings = [_finding(30, "shell injection")]
    expected = [
        ExpectedFinding(label="injection", line=30, keywords=["injection"]),
        ExpectedFinding(label="off-by-one", line=16, keywords=["off-by-one"]),
    ]
    score = score_fixture("f", findings, expected)
    assert score.recall == 0.5
    assert score.missed == ["off-by-one"]


def test_parse_fail_recorded_with_zero_findings() -> None:
    score = score_fixture("f", [], [_expected(30, ["injection"])], parsed_ok=False)
    assert score.parsed_ok is False
    assert score.recall == 0.0


def test_committed_badcode_fixture_manifest_is_valid() -> None:
    """The shipped fixture parses and its expected lines fall within the diff."""
    fixtures = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "badcode"
    manifest = Fixture.model_validate_json((fixtures / "expected.json").read_text())
    assert manifest.changed_file == "badcode.py"
    assert len(manifest.expected) >= 5
    assert all(e.keywords for e in manifest.expected)
