"""Severity ceilings for the advisory lenses.

The prompt already grades these lenses itself — complexity and ponytail `info`
to `medium`, documentation `info` to `low`, tests `low`/`medium` — but a graded
instruction is a request, not a constraint, and benchmark runs across a dozen
models are full of style commentary arriving at `high`, occasionally
`critical`.

An over-graded nit costs twice. It sits above a real bug wherever findings are
ordered by severity — the summary, the per-lens bound that drops the least
severe first — and it defeats `min_severity`, the one dial a team has for
turning advisory noise down, because noise graded as signal survives a floor
set at the signal.

So the grade is clamped here, deterministically, after the model has answered.
Clamping is deliberately not dropping: every finding is still posted, so recall
is untouched by construction. Only the claim about how much it matters changes,
back to the range the prompt asked for.

The lenses graded by impact — security, correctness, performance, intent, spec,
deprecation — are not capped. A bug is as severe as it is, and capping those
would hide exactly what the reviewer exists to find.
"""

from __future__ import annotations

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import ReviewCategory, ReviewFinding, Severity

_log = get_logger(__name__)

# Keyed by category, and only by category: in the `fast` preset an
# unattributed finding falls back to its GROUP id (`code-health`, `artefacts`),
# which spans capped and uncapped concerns — clamping a whole group would
# downgrade real bugs. A custom lens is likewise absent: it sets its own rules,
# and the engine has no rubric for it.
CATEGORY_SEVERITY_CEILING: dict[ReviewCategory, Severity] = {
    ReviewCategory.complexity: Severity.medium,
    ReviewCategory.ponytail: Severity.medium,
    ReviewCategory.documentation: Severity.medium,
    ReviewCategory.tests: Severity.medium,
}


def clamp_to_category_ceiling(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Lower any advisory finding graded above its category's ceiling."""
    out: list[ReviewFinding] = []
    for finding in findings:
        ceiling = CATEGORY_SEVERITY_CEILING.get(finding.category)  # type: ignore[arg-type]
        if ceiling is None or finding.severity.rank <= ceiling.rank:
            out.append(finding)
            continue
        _log.info(
            "advisory finding graded above its lens ceiling — lowering",
            extra={
                "category": finding.category,
                "claimed": finding.severity.value,
                "ceiling": ceiling.value,
            },
        )
        out.append(finding.model_copy(update={"severity": ceiling}))
    return out
