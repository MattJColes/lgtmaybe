"""The review use case lives outside the CLI package.

``lgtmaybe.review`` is the application layer: it runs a review over injected
ports, so any entry point (the Click CLI, the MCP server, the eval harness, a
library caller) drives the same flow. The CLI is only its composition root, so
the dependency points one way: ``cli`` imports ``review``, never the reverse.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import lgtmaybe.cli
import lgtmaybe.review
from lgtmaybe.core.models import Severity
from lgtmaybe.review import run_review
from tests.conftest import make_cfg
from tests.fakes import FakeEngine, FakeGitHub, FakeProvider

REVIEW_SOURCE = Path(lgtmaybe.review.__file__)
CLI_INIT = Path(lgtmaybe.cli.__file__)

#: The orchestration that moved out of ``cli/__init__.py``.
USE_CASE_FUNCTIONS = {
    "run_review",
    "run_describe",
    "run_diagram",
    "existing_overview_note",
    "fetch_context",
    "fail_on_check",
    "_incremental_context",
    "_diff_unchanged_since",
    "_diff_paths",
    "_validate_prior_findings",
    "_apply_learned_feedback",
}


def _imported_modules(path: Path) -> set[str]:
    """Every module ``path`` imports, lazy (in-function) imports included."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
            if node.module == "lgtmaybe":
                found.update(f"lgtmaybe.{alias.name}" for alias in node.names)
    return found


def _top_level_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_the_scan_sees_the_review_module_imports() -> None:
    """Guard the guard: an empty import set would pass the test below vacuously."""
    assert "lgtmaybe.core.ports" in _imported_modules(REVIEW_SOURCE)


def test_the_review_module_imports_nothing_from_the_cli() -> None:
    offending = sorted(
        module
        for module in _imported_modules(REVIEW_SOURCE)
        if module == "lgtmaybe.cli" or module.startswith("lgtmaybe.cli.")
    )
    assert not offending, f"lgtmaybe.review must not import the CLI: {offending}"


def test_importing_the_review_module_never_loads_the_cli() -> None:
    """Transitively too: a fresh interpreter importing it has no ``lgtmaybe.cli``."""
    check = (
        "import sys, lgtmaybe.review; "
        "sys.exit(1 if any(m == 'lgtmaybe.cli' or m.startswith('lgtmaybe.cli.') "
        "for m in sys.modules) else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", check],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"importing lgtmaybe.review loaded the CLI\n{proc.stderr}"


def test_the_review_module_defines_the_use_case() -> None:
    assert USE_CASE_FUNCTIONS <= _top_level_functions(REVIEW_SOURCE)


def test_the_cli_package_holds_no_review_orchestration() -> None:
    defined = _top_level_functions(CLI_INIT)
    # Guard the guard: the scan must see the CLI's own entry points.
    assert {"execute_review", "build_review_context"} <= defined
    plain = {name.lstrip("_") for name in USE_CASE_FUNCTIONS}
    leftover = {name for name in defined if name.lstrip("_") in plain}
    assert not leftover, f"review orchestration still defined in cli/__init__.py: {leftover}"


def test_run_review_runs_without_click_and_posts_the_review() -> None:
    """A second entry point calls the use case directly: no Click, no CLI context."""
    github = FakeGitHub()
    engine = FakeEngine(FakeProvider())

    findings, summary = run_review(github=github, engine=engine, cfg=make_cfg(), dry_run=False)

    assert findings
    assert github.posted == [(findings, summary)]
    assert github.posted_diffs == [github.get_pr_context().diff]


def test_run_review_dry_run_returns_without_posting() -> None:
    github = FakeGitHub()

    findings, summary = run_review(
        github=github, engine=FakeEngine(FakeProvider()), cfg=make_cfg(), dry_run=True
    )

    assert findings and summary
    assert github.posted == []
    assert github.check_runs == []


def test_run_review_creates_the_merge_gate_check_run() -> None:
    github = FakeGitHub()

    findings, _summary = run_review(
        github=github,
        engine=FakeEngine(FakeProvider()),
        cfg=make_cfg(fail_on=Severity.info),
        dry_run=False,
    )

    assert findings
    assert [run["conclusion"] for run in github.check_runs] == ["failure"]
