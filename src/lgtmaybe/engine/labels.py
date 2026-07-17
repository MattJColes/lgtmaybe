"""Review-effort and risk labels (F4), derived from the finished review.

No extra model calls: everything here is computed from the diff and the
findings the review already produced. Three labels:

- ``review-effort/1``–``5`` — a size estimate from the changed-line count, so
  reviewers can gauge a PR at a glance;
- ``possible-security-issue`` — a high/critical finding from the security
  lens posted this run;
- ``consider-splitting`` — the diff sprawls across many unrelated top-level
  directories, a hint that it bundles several themes.

The set is config-gated (``ReviewConfig.pr_labels``, default off) and applied
best-effort by the GitHub adapter — a labelling failure never fails a review.
"""

from __future__ import annotations

from lgtmaybe.core.models import (
    EFFORT_PREFIX,
    SECURITY_LABEL,
    SPLITTING_LABEL,
    PRContext,
    ReviewFinding,
    Severity,
)

# Changed-line thresholds for effort scores 2..5 (score 1 = below the first).
_EFFORT_THRESHOLDS = (50, 200, 500, 1000)

# "Sprawls across unrelated themes" heuristic: at least this many distinct
# top-level directories AND this many changed files. Deliberately conservative
# — a wrong split hint is pure noise.
_MIN_SPRAWL_DIRS = 4
_MIN_SPRAWL_FILES = 10


def compute_labels(findings: list[ReviewFinding], ctx: PRContext) -> list[str]:
    """The labels this review's outcome earns for the PR."""
    labels = [f"{EFFORT_PREFIX}{_effort_score(ctx.diff)}"]
    if any(f.category == "security" and f.severity >= Severity.high for f in findings):
        labels.append(SECURITY_LABEL)
    if _sprawls(ctx.changed_files):
        labels.append(SPLITTING_LABEL)
    return labels


def _effort_score(diff: str) -> int:
    """1–5 from the number of added/removed lines in *diff*."""
    changed = sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
    score = 1
    for threshold in _EFFORT_THRESHOLDS:
        if changed >= threshold:
            score += 1
    return score


def _sprawls(changed_files: list[str]) -> bool:
    """Whether the change set spans enough unrelated areas to suggest splitting."""
    if len(changed_files) < _MIN_SPRAWL_FILES:
        return False
    top_level = {path.split("/", 1)[0] for path in changed_files}
    return len(top_level) >= _MIN_SPRAWL_DIRS
