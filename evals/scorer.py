"""Pure scoring for the eval harness.

Given the findings a review produced and a fixture's manifest of *expected*
findings, compute how many expected issues were caught (recall) and whether the
model produced parseable output at all. No I/O, no model — unit-tested.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lgtmaybe.core.models import PRContext, Provider, ReviewFinding, Severity

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
    # On-disk corpus that backs this fixture's *unshown* files (a ``repo/`` subdir
    # next to ``diff.txt``). Populated by the loader, not the manifest JSON, so it is
    # excluded from (de)serialisation. When present the harness wires a read-only
    # reader + ast-grep symbol resolver rooted here, so a deferred cross-file verdict
    # can fetch the real definition — exactly the path symbol resolution adds.
    corpus_root: Path | None = Field(default=None, exclude=True)
    # HEAD text of the fixture's changed files (a ``head/`` subdir next to
    # ``diff.txt``), loader-populated like ``corpus_root``. When present it
    # becomes ``PRContext.file_contents`` — the input static-analysis fusion,
    # context expansion, and function-boundary padding all key on — so those
    # features can be A/B-measured against fixtures instead of running dark.
    head_root: Path | None = Field(default=None, exclude=True)


class FixtureScore(BaseModel):
    """The outcome of scoring one fixture's findings against its manifest.

    Precision answers the inverse of recall — *of the findings the reviewer made
    near issues we catalogued, how many were right?* It is deliberately scoped to
    "adjudicable" findings:

    - A finding is **adjudicable** if it lands within the line tolerance
      (:data:`_LINE_TOLERANCE`) of SOME catalogued line — an expected OR a
      forbidden one. A finding far from every catalogued line is **excluded** from
      precision (neither credited nor penalised): the fixture didn't enumerate
      that spot, so it may well be a legit extra catch, and punishing it would
      discourage real signal.
    - ``forbidden_count`` is how many findings fired a forbidden (cross-file
      false-positive) trap; ``unexpected_count`` is how many adjudicable findings
      matched neither an expected nor a forbidden entry — wrong findings on a line
      we DO know about.
    - ``precision = 1 - (forbidden_count + unexpected_count) / adjudicable_count``,
      clamped to ``[0, 1]``; it is ``1.0`` when ``adjudicable_count == 0`` (nothing
      to adjudicate). Precision is **reported, not gated** — ``run.py::_gate``
      keeps its parse/recall/clean bars unchanged.
    """

    name: str
    parsed_ok: bool
    expected_count: int
    matched_count: int
    findings_count: int
    missed: list[str]
    anchored_count: int = 0
    false_positives: list[str] = []
    # Adjudication counts (see class docstring). adjudicable = findings near some
    # catalogued line; forbidden = of those, ones that fired a forbidden trap;
    # unexpected = of those, ones matching neither expected nor forbidden.
    adjudicable_count: int = 0
    forbidden_count: int = 0
    unexpected_count: int = 0

    @property
    def recall(self) -> float:
        if self.expected_count == 0:
            return 1.0
        return self.matched_count / self.expected_count

    @property
    def precision(self) -> float:
        """Share of adjudicable findings that were right (1.0 when none adjudicable).

        ``1 - (forbidden + unexpected) / adjudicable``, clamped to ``[0, 1]``. Far-off
        findings are excluded, so an extra catch the fixture didn't list can't lower it.
        """
        if self.adjudicable_count == 0:
            return 1.0
        wrong = self.forbidden_count + self.unexpected_count
        return max(0.0, min(1.0, 1.0 - wrong / self.adjudicable_count))

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


def _near(finding: ReviewFinding, catalogued: list[ExpectedFinding]) -> bool:
    """True if *finding* lands within the line tolerance of any *catalogued* line.

    Line-only (keywords/severity ignored): this decides whether a finding is on a
    spot the fixture knows about — and so is *adjudicable* for precision — not
    whether it's a correct match.
    """
    return any(abs(finding.line - c.line) <= _LINE_TOLERANCE for c in catalogued)


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
    forbidden = forbidden or []
    false_positives = [fb.label for fb in forbidden if any(_matches(f, fb) for f in findings)]

    # Precision accounting: only findings near a catalogued (expected OR forbidden)
    # line are adjudicable. Of those, a finding is "wrong" if it fires a forbidden
    # trap OR matches no expected at all (an unexpected finding on a known line).
    catalogued = expected + forbidden
    adjudicable = forbidden_count = unexpected_count = 0
    for f in findings:
        if not _near(f, catalogued):
            continue  # far off — excluded from precision (could be a legit extra catch)
        adjudicable += 1
        if any(_matches(f, fb) for fb in forbidden):
            forbidden_count += 1
        elif not any(_matches(f, exp) for exp in expected):
            unexpected_count += 1

    return FixtureScore(
        name=name,
        parsed_ok=parsed_ok,
        expected_count=len(expected),
        matched_count=matched,
        findings_count=len(findings),
        missed=missed,
        anchored_count=sum(1 for f in findings if f.anchored),
        false_positives=false_positives,
        adjudicable_count=adjudicable,
        forbidden_count=forbidden_count,
        unexpected_count=unexpected_count,
    )


# ---------------------------------------------------------------------------
# Shared eval-runner plumbing (used by both run.py and rlm.py); co-located
# with Fixture, the manifest type they all take.
# ---------------------------------------------------------------------------


def _eval_ctx(diff: str, manifest: Fixture) -> PRContext:
    """The synthetic PRContext every eval review runs against (no real PR)."""
    file_contents: dict[str, str] = {}
    if manifest.head_root is not None:
        # The head/ dir mirrors the changed files' HEAD text — the same shape
        # the GitHub gateway fetches — feeding static analysis, context
        # expansion, and boundary padding during an eval run.
        file_contents = {
            str(p.relative_to(manifest.head_root)): p.read_text()
            for p in sorted(manifest.head_root.rglob("*"))
            if p.is_file()
        }
    return PRContext(
        diff=diff,
        changed_files=[manifest.changed_file],
        base_sha="0",
        head_sha="1",
        repo="eval/eval",
        pr_number=0,
        file_contents=file_contents,
    )


def _sampling_extra(
    provider: Provider,
    *,
    num_ctx: int | None,
    temperature: float | None,
    top_p: float | None,
    top_k: int | None,
) -> dict[str, Any]:
    """Forward only explicitly-given sampling params (plus ollama's num_ctx) to the model.

    Only forward params explicitly given so an unset flag keeps the model's own
    default rather than pinning it. litellm.drop_params drops a param a given
    provider can't take (e.g. top_k on an OpenAI-compat endpoint). num_ctx is
    ollama's context window — litellm rejects it for hosted providers.
    """
    extra: dict[str, Any] = {}
    if num_ctx is not None and provider is Provider.ollama:
        extra["num_ctx"] = num_ctx
    if temperature is not None:
        extra["temperature"] = temperature
    if top_p is not None:
        extra["top_p"] = top_p
    if top_k is not None:
        extra["top_k"] = top_k
    return extra


def _add_review_args(ap: argparse.ArgumentParser) -> None:
    """The review-driver CLI flags shared by evals.run and evals.rlm.

    Each runner adds its own extra flags around these (budget/repeats/only on
    rlm; min-recall/json/save-results/... on run).
    """
    ap.add_argument("--provider", required=True, choices=[p.value for p in Provider])
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-base", default=None)
    ap.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="per-request timeout (seconds); raise for slow local models on big diffs",
    )
    ap.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature forwarded to the model (default: the model's own)",
    )
    ap.add_argument(
        "--top-p",
        type=float,
        default=None,
        help="nucleus-sampling top_p forwarded to the model",
    )
    ap.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="top_k forwarded to the model (ollama/qwen3.x recommend 20 with thinking off)",
    )
    ap.add_argument(
        "--categories",
        default=None,
        help="comma-separated review lenses to run (default: all). Cuts the per-category "
        "fan-out for a fast CI smoke, e.g. 'security,correctness'.",
    )
    ap.add_argument(
        "--fixture",
        action="append",
        dest="fixtures",
        metavar="NAME",
        help="only run the named fixture(s); repeatable. Default: all. Lets CI run a fast "
        "single-fixture subset while the full set stays available on demand.",
    )
