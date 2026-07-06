"""Declarative finding post-processing rules (F5b).

``finding_rules`` is a safe, declarative alternative to arbitrary user hooks:
ordered rules whose ``match`` (path glob / category / title substring /
severity floor, ANDed) selects findings and whose ``action`` drops them or
remaps their severity — applied just before posting. No user code ever runs.
"""

from __future__ import annotations

from lgtmaybe.core.models import (
    FindingRule,
    Provider,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.engine.rules import apply_finding_rules


def _cfg(rules: list[dict[str, object]]) -> ReviewConfig:
    return ReviewConfig(
        provider=Provider.ollama,
        model="llama3",
        finding_rules=[FindingRule.model_validate(r) for r in rules],
    )


def _finding(
    path: str = "src/app.py",
    category: str | None = "correctness",
    title: str = "a bug",
    severity: Severity = Severity.medium,
) -> ReviewFinding:
    return ReviewFinding(
        path=path, line=1, severity=severity, title=title, body="b", category=category
    )


def test_no_rules_changes_nothing() -> None:
    findings = [_finding()]
    assert apply_finding_rules(findings, _cfg([])) == findings


def test_drop_by_path_glob() -> None:
    rules = [{"match": {"path": "tests/**"}, "action": {"drop": True}}]
    kept = apply_finding_rules(
        [_finding(path="tests/test_app.py"), _finding(path="src/app.py")], _cfg(rules)
    )
    assert [f.path for f in kept] == ["src/app.py"]


def test_glob_with_leading_globstar_matches_repo_root() -> None:
    rules = [{"match": {"path": "**/*.md"}, "action": {"drop": True}}]
    kept = apply_finding_rules([_finding(path="README.md")], _cfg(rules))
    assert kept == []


def test_drop_by_category() -> None:
    rules = [{"match": {"category": "complexity"}, "action": {"drop": True}}]
    kept = apply_finding_rules(
        [_finding(category="complexity"), _finding(category="security")], _cfg(rules)
    )
    assert [f.category for f in kept] == ["security"]


def test_drop_by_title_substring_is_case_insensitive() -> None:
    rules = [{"match": {"title_contains": "todo"}, "action": {"drop": True}}]
    kept = apply_finding_rules([_finding(title="Leftover TODO marker")], _cfg(rules))
    assert kept == []


def test_min_severity_matches_at_or_above() -> None:
    rules = [{"match": {"min_severity": "high"}, "action": {"drop": True}}]
    kept = apply_finding_rules(
        [_finding(severity=Severity.critical), _finding(severity=Severity.medium)],
        _cfg(rules),
    )
    assert [f.severity for f in kept] == [Severity.medium]


def test_match_fields_are_anded() -> None:
    rules = [{"match": {"path": "tests/**", "category": "complexity"}, "action": {"drop": True}}]
    kept = apply_finding_rules(
        [
            _finding(path="tests/test_app.py", category="complexity"),  # both → dropped
            _finding(path="tests/test_app.py", category="security"),  # path only → kept
            _finding(path="src/app.py", category="complexity"),  # category only → kept
        ],
        _cfg(rules),
    )
    assert len(kept) == 2


def test_severity_remap() -> None:
    rules = [{"match": {"category": "documentation"}, "action": {"set_severity": "info"}}]
    kept = apply_finding_rules(
        [_finding(category="documentation", severity=Severity.medium)], _cfg(rules)
    )
    assert kept[0].severity is Severity.info


def test_rules_apply_in_order() -> None:
    """A remap by an earlier rule changes what a later severity-matched rule sees."""
    rules = [
        {"match": {"category": "performance"}, "action": {"set_severity": "info"}},
        {"match": {"min_severity": "high"}, "action": {"drop": True}},
    ]
    kept = apply_finding_rules(
        [_finding(category="performance", severity=Severity.high)], _cfg(rules)
    )
    # Remapped to info before the drop rule ran — it no longer matches.
    assert len(kept) == 1
    assert kept[0].severity is Severity.info


def test_empty_match_matches_everything() -> None:
    rules = [{"action": {"drop": True}}]
    assert apply_finding_rules([_finding(), _finding(path="x.py")], _cfg(rules)) == []
