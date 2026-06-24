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


def test_forbidden_finding_flagged_as_false_positive() -> None:
    """A produced finding matching a forbidden entry is a cross-file false positive."""
    findings = [_finding(13, "model_dump may pass fields absent from V2")]
    forbidden = [_expected(13, ["model_dump", "absent"])]
    score = score_fixture("f", findings, [], forbidden=forbidden)
    assert score.false_positives == ["line 13"]
    assert score.clean is False


def test_no_false_positive_when_forbidden_not_triggered() -> None:
    """A clean review (no finding matches a forbidden trap) records no false positive."""
    findings = [_finding(14, "secret api_token logged")]
    forbidden = [_expected(13, ["model_dump", "absent"])]
    score = score_fixture("f", findings, [], forbidden=forbidden)
    assert score.false_positives == []
    assert score.clean is True


def test_forbidden_respects_line_and_keyword() -> None:
    """A forbidden keyword on a far-off line is not a false positive (precision)."""
    findings = [_finding(40, "model_dump may pass fields absent from V2")]  # far from line 13
    forbidden = [_expected(13, ["model_dump", "absent"])]
    score = score_fixture("f", findings, [], forbidden=forbidden)
    assert score.false_positives == []
    assert score.clean is True


def test_precision_is_one_with_only_expected_findings() -> None:
    """Every produced finding lands on an expected catch — nothing wrong fired."""
    findings = [_finding(30, "shell injection")]
    expected = [_expected(30, ["injection"])]
    score = score_fixture("f", findings, expected)
    assert score.precision == 1.0
    assert score.adjudicable_count == 1
    assert score.unexpected_count == 0
    assert score.forbidden_count == 0


def test_precision_drops_on_a_forbidden_hit() -> None:
    """A forbidden finding firing is a wrong adjudicable finding — precision drops."""
    findings = [_finding(13, "model_dump may pass fields absent from V2")]
    forbidden = [_expected(13, ["model_dump", "absent"])]
    score = score_fixture("f", findings, [], forbidden=forbidden)
    assert score.forbidden_count == 1
    assert score.adjudicable_count == 1
    assert score.precision == 0.0


def test_precision_drops_on_unexpected_finding_on_a_known_line() -> None:
    """A spurious finding near a catalogued line (but matching nothing) is penalised."""
    # One genuine catch on line 30, plus a junk finding on line 30 (a known/expected
    # line) whose keyword matches neither expected nor forbidden — an unexpected FP.
    findings = [_finding(30, "shell injection"), _finding(30, "style nit about naming")]
    expected = [_expected(30, ["injection"])]
    score = score_fixture("f", findings, expected)
    assert score.adjudicable_count == 2
    assert score.unexpected_count == 1
    assert score.forbidden_count == 0
    assert score.precision == 0.5


def test_far_off_finding_leaves_precision_at_one() -> None:
    """A finding nowhere near a catalogued line is excluded from precision entirely —
    a legit extra catch the fixture didn't enumerate is neither credited nor punished."""
    findings = [_finding(30, "shell injection"), _finding(99, "some other real catch")]
    expected = [_expected(30, ["injection"])]
    score = score_fixture("f", findings, expected)
    assert score.adjudicable_count == 1  # only the line-30 finding is adjudicable
    assert score.unexpected_count == 0
    assert score.precision == 1.0


def test_precision_is_one_when_nothing_is_adjudicable() -> None:
    """No produced finding lands near any catalogued line → precision is vacuously 1.0."""
    findings = [_finding(99, "far away")]
    expected = [_expected(30, ["injection"])]
    score = score_fixture("f", findings, expected)
    assert score.adjudicable_count == 0
    assert score.precision == 1.0


def test_precision_clamps_at_zero() -> None:
    """More wrong adjudicable findings than the count can't drive precision negative."""
    findings = [
        _finding(13, "model_dump may pass fields absent from V2"),
        _finding(13, "junk one"),
        _finding(13, "junk two"),
    ]
    forbidden = [_expected(13, ["model_dump", "absent"])]
    score = score_fixture("f", findings, [], forbidden=forbidden)
    # All three land on the forbidden line 13: 1 forbidden + 2 unexpected = 3 wrong
    # over 3 adjudicable → 1 - 3/3 = 0, clamped (never negative).
    assert score.precision == 0.0


def test_precision_unchanged_recall_and_clean_semantics() -> None:
    """Precision is additive — recall, clean and false_positives keep their meaning."""
    findings = [_finding(30, "shell injection"), _finding(13, "model_dump absent from V2")]
    expected = [_expected(30, ["injection"])]
    forbidden = [_expected(13, ["model_dump", "absent"])]
    score = score_fixture("f", findings, expected, forbidden=forbidden)
    assert score.recall == 1.0
    assert score.clean is False
    assert score.false_positives == ["line 13"]
    assert score.precision == 0.5  # one right (line 30), one forbidden (line 13)


def test_committed_cross_file_fp_fixture_manifest_is_valid() -> None:
    """The cross-file FP fixture parses and carries both expected and forbidden."""
    fixtures = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "cross-file-fp"
    manifest = Fixture.model_validate_json((fixtures / "expected.json").read_text())
    assert manifest.changed_file == "migrations/0003_backfill.py"
    assert manifest.expected, "fixture needs a genuine in-diff finding"
    assert manifest.forbidden, "fixture needs forbidden (cross-file FP) traps"
    assert all(e.keywords for e in manifest.expected)
    assert all(f.keywords for f in manifest.forbidden)


def test_committed_badcode_fixture_manifest_is_valid() -> None:
    """The shipped fixture parses and its expected lines fall within the diff."""
    fixtures = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "badcode"
    manifest = Fixture.model_validate_json((fixtures / "expected.json").read_text())
    assert manifest.changed_file == "badcode.py"
    assert len(manifest.expected) >= 5
    assert all(e.keywords for e in manifest.expected)
