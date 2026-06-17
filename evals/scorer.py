"""Pure scoring for the eval harness.

Given the findings a review produced and a fixture's manifest of *expected*
findings, compute how many expected issues were caught (recall) and whether the
model produced parseable output at all. No I/O, no model — unit-tested.
"""

from __future__ import annotations

from pydantic import BaseModel

from lgtmaybe.core.models import ReviewFinding, Severity

# How far a reported line may drift from the expected line and still count — models
# attribute a finding to a nearby line (the call vs the def) often enough.
_LINE_TOLERANCE = 3


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


class FixtureScore(BaseModel):
    """The outcome of scoring one fixture's findings against its manifest."""

    name: str
    parsed_ok: bool
    expected_count: int
    matched_count: int
    findings_count: int
    missed: list[str]
    anchored_count: int = 0

    @property
    def recall(self) -> float:
        if self.expected_count == 0:
            return 1.0
        return self.matched_count / self.expected_count

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
    parsed_ok: bool = True,
) -> FixtureScore:
    """Score *findings* against the *expected* manifest for one fixture."""
    matched = 0
    missed: list[str] = []
    for exp in expected:
        if any(_matches(f, exp) for f in findings):
            matched += 1
        else:
            missed.append(exp.label)
    return FixtureScore(
        name=name,
        parsed_ok=parsed_ok,
        expected_count=len(expected),
        matched_count=matched,
        findings_count=len(findings),
        missed=missed,
        anchored_count=sum(1 for f in findings if f.anchored),
    )
