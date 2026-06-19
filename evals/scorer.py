"""Pure scoring for the eval harness.

Given the findings a review produced and a fixture's manifest of *expected*
findings, compute how many expected issues were caught (recall) and whether the
model produced parseable output at all. No I/O, no model — unit-tested.
"""

from __future__ import annotations

from pydantic import BaseModel

from lgtmaybe.core.models import ReviewFinding, Severity

# How far a reported line may drift from the expected line and still count. The
# engine re-anchors findings to the exact changed line they quote (see
# engine._snap_findings), so a correctly-placed finding lands on its line — this
# is tight on purpose to reward that. The ±1 slack only absorbs the def-vs-first-
# statement ambiguity of a multi-line issue, which the fixture author picks one
# line for. A finding the model couldn't anchor is demoted, not mis-placed, so it
# never reaches here with a wrong line.
_LINE_TOLERANCE = 1


class ExpectedFinding(BaseModel):
    """One issue a fixture expects the reviewer to catch."""

    label: str  # human description, e.g. "off-by-one in average()"
    line: int
    keywords: list[str]  # matches if ANY appears (case-insensitive) in title+body
    severity_at_least: Severity | None = None


class Fixture(BaseModel):
    """A fixture manifest: the changed file and the issues it plants."""

    name: str
    changed_file: str
    expected: list[ExpectedFinding]
    # Findings that must NOT appear: cross-file false-positive traps. The diff
    # looks like it omits a guard/field/check, but the handling lives in an
    # unshown file, so a correct reviewer stays silent. A produced finding that
    # matches one of these is a regression (see engine codebase-humility rules).
    forbidden: list[ExpectedFinding] = []


class FixtureScore(BaseModel):
    """The outcome of scoring one fixture's findings against its manifest."""

    name: str
    parsed_ok: bool
    expected_count: int
    matched_count: int
    findings_count: int
    missed: list[str]
    anchored_count: int = 0
    false_positives: list[str] = []

    @property
    def recall(self) -> float:
        if self.expected_count == 0:
            return 1.0
        return self.matched_count / self.expected_count

    @property
    def clean(self) -> bool:
        """True when no forbidden (cross-file false-positive) finding fired."""
        return not self.false_positives

    @property
    def anchored_rate(self) -> float:
        """Share of findings the engine could place inline (anchor matched a line).

        A low rate means the model's quoted anchors aren't matching the diff, so
        many findings get demoted to the summary instead of an inline comment —
        the dial to watch when tuning the line-anchoring fix.
        """
        if self.findings_count == 0:
            return 1.0
        return self.anchored_count / self.findings_count


def _matches(finding: ReviewFinding, expected: ExpectedFinding) -> bool:
    """True if *finding* plausibly reports *expected* (line + keyword + severity)."""
    if abs(finding.line - expected.line) > _LINE_TOLERANCE:
        return False
    haystack = f"{finding.title} {finding.body}".lower()
    if expected.keywords and not any(k.lower() in haystack for k in expected.keywords):
        return False
    if expected.severity_at_least is not None and not (
        finding.severity >= expected.severity_at_least
    ):
        return False
    return True


def score_fixture(
    name: str,
    findings: list[ReviewFinding],
    expected: list[ExpectedFinding],
    *,
    forbidden: list[ExpectedFinding] | None = None,
    parsed_ok: bool = True,
) -> FixtureScore:
    """Score *findings* against the *expected* manifest for one fixture.

    Recall counts how many *expected* issues were caught. *forbidden* findings are
    the inverse: any produced finding matching one is a false positive (a cross-file
    claim the reviewer should not have made), recorded in ``false_positives``.
    """
    matched = 0
    missed: list[str] = []
    for exp in expected:
        if any(_matches(f, exp) for f in findings):
            matched += 1
        else:
            missed.append(exp.label)
    false_positives = [
        fb.label for fb in (forbidden or []) if any(_matches(f, fb) for f in findings)
    ]
    return FixtureScore(
        name=name,
        parsed_ok=parsed_ok,
        expected_count=len(expected),
        matched_count=matched,
        findings_count=len(findings),
        missed=missed,
        anchored_count=sum(1 for f in findings if f.anchored),
        false_positives=false_positives,
    )
