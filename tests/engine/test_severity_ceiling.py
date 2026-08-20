"""The advisory lenses cannot out-shout the ones that find bugs.

The prompt grades the style lenses itself — complexity and ponytail `info` to
`medium`, documentation `info` to `low`, tests `low`/`medium` — but nothing
enforced it, and benchmark runs are full of style commentary arriving at `high`
and occasionally `critical`.

That costs twice. A `critical` "this function is a bit long" sits above a real
SQL injection in a severity-ordered summary; and `min_severity`, the one dial a
team has for turning the noise down, cannot filter noise that arrives graded as
the signal. Clamping is deliberately not dropping: every finding is still
posted, so recall is untouched by construction — only the grade changes.
"""

from __future__ import annotations

import pytest

from lgtmaybe.core.models import ReviewCategory, ReviewFinding, Severity
from lgtmaybe.engine.severity import CATEGORY_SEVERITY_CEILING, clamp_to_category_ceiling


def _finding(category: str, severity: Severity) -> ReviewFinding:
    return ReviewFinding(
        path="a.py",
        line=1,
        side="RIGHT",
        severity=severity,
        title="t",
        body="b",
        category=category,
    )


@pytest.mark.parametrize("category", ["complexity", "ponytail", "documentation", "tests"])
def test_an_advisory_lens_cannot_claim_more_than_its_ceiling(category: str) -> None:
    ceiling = CATEGORY_SEVERITY_CEILING[category]

    clamped = clamp_to_category_ceiling([_finding(category, Severity.critical)])

    assert clamped[0].severity is ceiling


@pytest.mark.parametrize("category", ["security", "correctness", "performance", "intent", "spec"])
def test_a_lens_that_grades_by_impact_is_left_alone(category: str) -> None:
    """Performance is graded by impact up to `high`, and a security or
    correctness bug is as severe as it is — clamping those would hide bugs."""
    clamped = clamp_to_category_ceiling([_finding(category, Severity.critical)])

    assert clamped[0].severity is Severity.critical


def test_a_finding_already_within_its_ceiling_is_untouched() -> None:
    finding = _finding("complexity", Severity.info)

    assert clamp_to_category_ceiling([finding])[0] is finding


def test_nothing_is_dropped() -> None:
    """Clamping trades grade for noise, never coverage."""
    findings = [_finding("tests", Severity.high), _finding("security", Severity.high)]

    assert len(clamp_to_category_ceiling(findings)) == 2


def test_a_merged_lens_id_is_not_mistaken_for_a_category() -> None:
    """In the fast preset a finding the model did not attribute falls back to
    the GROUP id (`code-health`, `artefacts`), which spans capped and uncapped
    concerns — clamping the whole group would silently downgrade a real bug."""
    clamped = clamp_to_category_ceiling([_finding("code-health", Severity.high)])

    assert clamped[0].severity is Severity.high


def test_a_custom_lens_is_not_capped() -> None:
    """A user-defined lens sets its own rules; the engine has no rubric for it."""
    clamped = clamp_to_category_ceiling([_finding("our-house-style", Severity.critical)])

    assert clamped[0].severity is Severity.critical


def test_the_ceilings_cover_every_advisory_category_and_nothing_else() -> None:
    assert set(CATEGORY_SEVERITY_CEILING) == {
        ReviewCategory.complexity,
        ReviewCategory.ponytail,
        ReviewCategory.documentation,
        ReviewCategory.tests,
    }


def test_a_review_posts_the_clamped_grade_not_the_claimed_one() -> None:
    """End to end through the engine, where the stamping happens."""
    import json

    from lgtmaybe.core.models import PRContext, ProviderResult
    from lgtmaybe.engine import LLMReviewEngine
    from tests.conftest import make_cfg
    from tests.fakes import FakeProvider

    diff = (
        "diff --git a/a.py b/a.py\n"
        "index 111..222 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import os\n"
        "+def helper(a, b, c, d, e): pass\n"
    )
    payload = json.dumps(
        {
            "findings": [
                {
                    "path": "a.py",
                    "line": 2,
                    "side": "RIGHT",
                    "severity": "critical",
                    "title": "This helper takes too many parameters",
                    "body": "Long parameter list.",
                    "anchor": "def helper(a, b, c, d, e): pass",
                }
            ]
        }
    )
    provider = FakeProvider(result=ProviderResult(text=payload, input_tokens=1, output_tokens=1))
    ctx = PRContext(
        diff=diff,
        changed_files=["a.py"],
        base_sha="base",
        head_sha="head",
        repo="org/repo",
        pr_number=1,
    )

    findings, _summary = LLMReviewEngine(provider).review(
        ctx, make_cfg(categories=[ReviewCategory.complexity], reflect=False)
    )

    assert [f.severity for f in findings] == [Severity.medium]
