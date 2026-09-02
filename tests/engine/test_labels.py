"""Review-effort and risk labels (F4) — derived from data already computed.

``compute_labels`` turns the finished review into PR labels with **no extra
model calls**: a ``review-effort/1``-``5`` size estimate from the diff, a
``possible-security-issue`` flag when a high/critical security-lens finding
posts, and a ``consider-splitting`` hint when the diff sprawls across many
unrelated top-level directories. Config-gated (``pr_labels``, default off).
"""

from __future__ import annotations

from lgtmaybe.core.models import PRContext, ReviewFinding, Severity
from lgtmaybe.engine.labels import compute_labels

_SMALL_DIFF = "diff --git a/a.py b/a.py\n@@ -1 +1,2 @@\n old\n+new\n"


def _ctx(diff: str = _SMALL_DIFF, files: list[str] | None = None) -> PRContext:
    return PRContext(
        diff=diff,
        changed_files=files or ["a.py"],
        base_sha="abc",
        head_sha="def",
        repo="org/repo",
        pr_number=12,
    )


def _finding(category: str | None, severity: Severity) -> ReviewFinding:
    return ReviewFinding(
        path="a.py",
        line=1,
        severity=severity,
        title="t",
        body="b",
        category=category,
    )


def _diff_of_lines(n: int) -> str:
    body = "\n".join(f"+line {i}" for i in range(n))
    return f"diff --git a/a.py b/a.py\n@@ -1 +1,{n} @@\n{body}\n"


def test_effort_scales_with_changed_lines() -> None:
    assert "review-effort/1" in compute_labels([], _ctx(_diff_of_lines(10)))
    assert "review-effort/2" in compute_labels([], _ctx(_diff_of_lines(100)))
    assert "review-effort/3" in compute_labels([], _ctx(_diff_of_lines(300)))
    assert "review-effort/4" in compute_labels([], _ctx(_diff_of_lines(700)))
    assert "review-effort/5" in compute_labels([], _ctx(_diff_of_lines(1500)))


def test_high_security_finding_adds_the_security_label() -> None:
    labels = compute_labels([_finding("security", Severity.high)], _ctx())
    assert "possible-security-issue" in labels

    labels = compute_labels([_finding("security", Severity.critical)], _ctx())
    assert "possible-security-issue" in labels


def test_non_security_or_low_findings_do_not_add_the_security_label() -> None:
    # High severity but a correctness finding — not the security lens.
    assert "possible-security-issue" not in compute_labels(
        [_finding("correctness", Severity.high)], _ctx()
    )
    # Security lens but below high.
    assert "possible-security-issue" not in compute_labels(
        [_finding("security", Severity.medium)], _ctx()
    )
    # No category information at all (reflect-off legacy path) — stay quiet.
    assert "possible-security-issue" not in compute_labels(
        [_finding(None, Severity.critical)], _ctx()
    )


def test_sprawling_diff_gets_the_consider_splitting_hint() -> None:
    files = [f"pkg{i}/mod{j}.py" for i in range(5) for j in range(3)]  # 5 dirs, 15 files
    diff = "".join(f"diff --git a/{p} b/{p}\n@@ -1 +1,2 @@\n old\n+new\n" for p in files)
    labels = compute_labels([], _ctx(diff, files))
    assert "consider-splitting" in labels


def test_focused_diff_gets_no_splitting_hint() -> None:
    files = [f"pkg/mod{j}.py" for j in range(12)]  # many files, one theme
    labels = compute_labels([], _ctx(_SMALL_DIFF, files))
    assert "consider-splitting" not in labels


def test_root_files_do_not_count_as_top_level_directories() -> None:
    files = [f"config{i}.yml" for i in range(10)]
    labels = compute_labels([], _ctx(_SMALL_DIFF, files))
    assert "consider-splitting" not in labels


def test_a_leaked_secret_earns_the_security_label() -> None:
    """The most label-worthy finding lgtmaybe can produce must not be missed.

    The label predicate keyed on the literal "security" category, which no
    deterministic secret scanner ever carries.
    """
    from lgtmaybe.engine.labels import SECURITY_LABEL, compute_labels

    finding = ReviewFinding(
        path="src/app.py",
        line=2,
        severity=Severity.high,
        title="gitleaks: aws-access-key-id",
        body="AWS Access Key",
        category="scan:gitleaks",
    )
    ctx = PRContext(
        diff="diff --git a/src/app.py b/src/app.py\n@@ -1 +1,2 @@\n a\n+b\n",
        changed_files=["src/app.py"],
        base_sha="a",
        head_sha="b",
        repo="org/repo",
        pr_number=1,
    )

    assert SECURITY_LABEL in compute_labels([finding], ctx)
