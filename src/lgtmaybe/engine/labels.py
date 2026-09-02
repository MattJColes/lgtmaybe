"""Review-effort and risk labels (F4), derived from the finished review.

No extra model calls: everything here is computed from the diff and the
findings the review already produced. Three labels:

- ``review-effort/1``–``5`` — a size estimate from the changed-line count, so
  reviewers can gauge a PR at a glance;
- ``possible-security-issue`` — a high/critical security finding posted this
  run, from the security lens or from a secret/SAST scanner;
- ``consider-splitting`` — the diff sprawls across many unrelated top-level
  directories, a hint that it bundles several themes.

The set is config-gated (``ReviewConfig.pr_labels``, default off) and applied
best-effort by the GitHub adapter — a labelling failure never fails a review.
"""

from __future__ import annotations

from lgtmaybe.core.diffparse import changed_line_count
from lgtmaybe.core.models import (
    _SCAN_CATEGORY_PREFIX,
    EFFORT_PREFIX,
    SECURITY_LABEL,
    SPLITTING_LABEL,
    PRContext,
    ReviewCategory,
    ReviewFinding,
    Severity,
    StaticAnalysisTool,
)

# Scanner categories whose findings are security findings by nature. Listed
# rather than inferred: a type checker or a linter also carries a `scan:`
# category, and a mypy error is not a security issue.
_SECURITY_SCAN_CATEGORIES: frozenset[str] = frozenset(
    f"{_SCAN_CATEGORY_PREFIX}{tool.value}"
    for tool in (StaticAnalysisTool.gitleaks, StaticAnalysisTool.bandit, StaticAnalysisTool.semgrep)
)

# Changed-line thresholds for effort scores 2..5 (score 1 = below the first).
_EFFORT_THRESHOLDS = (50, 200, 500, 1000)

# "Sprawls across unrelated themes" heuristic: at least this many distinct
# top-level directories AND this many changed files. Deliberately conservative
# — a wrong split hint is pure noise.
_MIN_SPRAWL_DIRS = 4
_MIN_SPRAWL_FILES = 10


def is_security_finding(finding: ReviewFinding) -> bool:
    """Whether *finding* is a security finding, from the lens or from a scanner.

    A secret scanner's finding is the most label-worthy thing this reviewer
    produces, and it never carries the literal ``security`` lens category — so
    matching on that alone silently skipped exactly the case the label is for.
    """
    category = finding.category or ""
    return category == ReviewCategory.security.value or category in _SECURITY_SCAN_CATEGORIES


def compute_labels(findings: list[ReviewFinding], ctx: PRContext) -> list[str]:
    """The labels this review's outcome earns for the PR."""
    labels = [f"{EFFORT_PREFIX}{_effort_score(ctx.diff)}"]
    if any(is_security_finding(f) and f.severity >= Severity.high for f in findings):
        labels.append(SECURITY_LABEL)
    if _sprawls(ctx.changed_files):
        labels.append(SPLITTING_LABEL)
    return labels


def _effort_score(diff: str) -> int:
    """1–5 from the number of added/removed lines in *diff*."""
    changed = changed_line_count(diff)
    score = 1
    for threshold in _EFFORT_THRESHOLDS:
        if changed >= threshold:
            score += 1
    return score


def _sprawls(changed_files: list[str]) -> bool:
    """Whether the change set spans enough unrelated areas to suggest splitting."""
    if len(changed_files) < _MIN_SPRAWL_FILES:
        return False
    top_level = {path.split("/", 1)[0] for path in changed_files if "/" in path}
    return len(top_level) >= _MIN_SPRAWL_DIRS
