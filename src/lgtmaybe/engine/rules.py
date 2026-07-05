"""Declarative finding post-processing (F5b).

``ReviewConfig.finding_rules`` lets a team filter or re-grade findings before
posting — drop the complexity lens in test files, downgrade documentation
nits, and so on — through pure declarative match/action rules. Deliberately
NOT an arbitrary post-processing hook: rules can only drop or remap severity,
so config stays data and no user code ever executes (executing user Python
would widen the attack surface the reviewer exists to guard).
"""

from __future__ import annotations

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import FindingRule, ReviewConfig, ReviewFinding

from .engine import _matches_glob  # same package; the one canonical glob matcher

_log = get_logger(__name__)


def apply_finding_rules(findings: list[ReviewFinding], cfg: ReviewConfig) -> list[ReviewFinding]:
    """Run *findings* through the configured rules, in order.

    A ``drop`` removes the finding immediately; a ``set_severity`` remaps it
    and later rules see the new severity. With no rules (the default) the
    input is returned unchanged.
    """
    if not cfg.finding_rules:
        return findings

    kept: list[ReviewFinding] = []
    dropped = 0
    for finding in findings:
        out: ReviewFinding | None = finding
        for rule in cfg.finding_rules:
            if out is None:
                break
            if not _matches(rule, out):
                continue
            if rule.action.drop:
                out = None
            elif rule.action.set_severity is not None:
                out = out.model_copy(update={"severity": rule.action.set_severity})
        if out is None:
            dropped += 1
        else:
            kept.append(out)
    if dropped:
        _log.info("finding rules dropped findings", extra={"count": dropped})
    return kept


def _matches(rule: FindingRule, finding: ReviewFinding) -> bool:
    match = rule.match
    if match.path is not None and not _matches_glob(finding.path, match.path):
        return False
    if match.category is not None and finding.category != match.category:
        return False
    if (
        match.title_contains is not None
        and match.title_contains.lower() not in finding.title.lower()
    ):
        return False
    return match.min_severity is None or finding.severity >= match.min_severity
