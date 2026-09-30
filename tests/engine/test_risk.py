"""Risk of change: a deterministic verdict from blast radius, paths, size and tests.

The scan tests run the real ast-grep binary (a core dependency) over a
``tmp_path`` workspace, because import resolution is only worth testing against
the parser it depends on. Failure modes inject a scanner instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lgtmaybe.core.models import PRContext, RiskAssessment, RiskConfig, RiskReason
from lgtmaybe.engine import risk as risk_module
from lgtmaybe.engine.risk import (
    assess_risk,
    is_doc_path,
    is_test_path,
    render_risk_section,
    risk_marker,
    risk_segment,
)
from tests.conftest import make_cfg


def _patch(path: str, lines: int = 1, *, added: bool = False, deleted: bool = False) -> str:
    old = "/dev/null" if added else f"a/{path}"
    new = "/dev/null" if deleted else f"b/{path}"
    marker = "-" if deleted else "+"
    body = "".join(f"{marker}line {i}\n" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n--- {old}\n+++ {new}\n@@ -0,0 +1,{lines} @@\n{body}"


def _ctx(*patches: tuple[str, int] | tuple[str, int, str]) -> PRContext:
    """A context from ``(path, changed_lines[, "added"|"deleted"])`` tuples."""
    diff = ""
    for spec in patches:
        path, lines = spec[0], spec[1]
        flag = spec[2] if len(spec) > 2 else ""
        diff += _patch(path, lines, added=flag == "added", deleted=flag == "deleted")
    return PRContext(
        diff=diff,
        changed_files=[spec[0] for spec in patches],
        base_sha="a",
        head_sha="b",
        repo="o/r",
        pr_number=1,
    )


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _cfg(**risk: object):  # type: ignore[no-untyped-def]
    return make_cfg(risk=RiskConfig(**risk))  # type: ignore[arg-type]


def _texts(result: RiskAssessment) -> list[str]:
    return [reason.text for reason in result.reasons]


# --- blast radius: Python -------------------------------------------------------


def _python_workspace(root: Path, importers: int) -> Path:
    files = {"src/pkg/__init__.py": "", "src/pkg/core.py": "X = 1\n"}
    shapes = [
        "from pkg import core\n",
        "import pkg.core\n",
        "from pkg.core import X\n",
        "import pkg.core as c, os\n",
        "from pkg import (\n    core,  # the core\n)\n",
    ]
    for i in range(importers):
        files[f"src/app/mod{i}.py"] = shapes[i % len(shapes)]
    return _write(root, files)


def test_python_importers_of_every_shape_are_counted(tmp_path: Path) -> None:
    root = _python_workspace(tmp_path, 5)
    result = assess_risk(_ctx(("src/pkg/core.py", 1)), _cfg(), root)
    assert result.level == "medium"
    assert "`src/pkg/core.py` is imported by 5 files" in _texts(result)


def test_relative_imports_resolve_from_the_importing_package(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/core.py": "",
            "src/pkg/a.py": "from . import core\n",
            "src/pkg/b.py": "from .core import X\n",
            "src/pkg/sub/__init__.py": "",
            "src/pkg/sub/c.py": "from ..core import X\n",
            "src/pkg/sub/d.py": "from .. import core\n",
            "src/pkg/f.py": "from . import core as c, other\n",
            "src/pkg/e.py": "from .other import core\n",  # a different module
        },
    )
    result = assess_risk(_ctx(("src/pkg/core.py", 1)), _cfg(), root)
    assert "`src/pkg/core.py` is imported by 5 files" in _texts(result)


def test_a_top_level_name_does_not_match_a_module_inside_a_package(tmp_path: Path) -> None:
    """`import core` cannot mean `src/pkg/core.py` when `src/pkg` is a package."""
    files = {"src/pkg/__init__.py": "", "src/pkg/core.py": ""}
    files |= {f"tools/t{i}.py": "import core\n" for i in range(6)}
    result = assess_risk(_ctx(("src/pkg/core.py", 1)), _cfg(), _write(tmp_path, files))
    assert result.level == "low"


def test_twenty_importers_is_high(tmp_path: Path) -> None:
    root = _python_workspace(tmp_path, 20)
    result = assess_risk(_ctx(("src/pkg/core.py", 1)), _cfg(), root)
    assert result.level == "high"
    assert "`src/pkg/core.py` is imported by 20 files" in _texts(result)


def test_the_file_itself_and_files_the_pr_changes_are_not_importers(tmp_path: Path) -> None:
    root = _python_workspace(tmp_path, 5)
    (root / "src/pkg/core.py").write_text("import pkg.core\n")
    ctx = _ctx(("src/pkg/core.py", 1), ("src/app/mod0.py", 1))
    result = assess_risk(ctx, _cfg(), root)
    assert result.level == "low"  # 4 importers left: below the medium bar


def test_test_files_are_not_counted_as_importers(tmp_path: Path) -> None:
    """Blast radius is the production code a change can break; a test that
    imports the module is its coverage, not its dependents."""
    files = {"src/pkg/__init__.py": "", "src/pkg/core.py": ""}
    files |= {f"tests/test_core{i}.py": "from pkg import core\n" for i in range(6)}
    result = assess_risk(_ctx(("src/pkg/core.py", 1)), _cfg(), _write(tmp_path, files))
    assert result.level == "low"


def test_a_file_the_pr_adds_has_no_importers_and_is_not_unassessed(tmp_path: Path) -> None:
    """Measured zero, not unmeasured — on a head workspace the PR's own importers
    exist, but they are the PR's, already in its diff."""
    root = _python_workspace(tmp_path, 12)
    result = assess_risk(_ctx(("src/pkg/core.py", 1, "added")), _cfg(), root)
    assert result.level == "low"
    assert result.unassessed == []


# --- blast radius: TypeScript / JavaScript --------------------------------------


def test_ts_and_js_relative_imports_are_counted(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "web/lib/api.ts": "export const x = 1\n",
            "web/a.ts": "import { x } from './lib/api'\n",
            "web/b.tsx": "import type { T } from './lib/api.js'\n",
            "web/c.js": "const api = require('./lib/api')\n",
            "web/d.ts": "export { x } from './lib/api'\n",
            "web/e/f.ts": "const m = await import('../lib/api')\n",
            "web/g.ts": "import api from 'api'\n",  # bare specifier: a package
        },
    )
    result = assess_risk(_ctx(("web/lib/api.ts", 1)), _cfg(), root)
    assert "`web/lib/api.ts` is imported by 5 files" in _texts(result)


def test_index_files_resolve_from_their_directory(tmp_path: Path) -> None:
    files = {"web/util/index.ts": ""}
    files |= {f"web/m{i}.ts": "import u from './util'\n" for i in range(5)}
    result = assess_risk(_ctx(("web/util/index.ts", 1)), _cfg(), _write(tmp_path, files))
    assert "`web/util/index.ts` is imported by 5 files" in _texts(result)


# --- unassessed -----------------------------------------------------------------


def test_a_modified_file_missing_from_the_workspace_is_unassessed(tmp_path: Path) -> None:
    result = assess_risk(_ctx(("src/app.py", 1)), _cfg(), tmp_path)
    assert result.unassessed == ["src/app.py"]
    assert result.level == "low"
    assert "blast radius not assessed" in risk_segment(result)


def test_an_unsupported_language_is_unassessed_and_low_carries_the_caveat(
    tmp_path: Path,
) -> None:
    root = _write(tmp_path, {"lib/billing.rb": "class Billing; end\n"})
    result = assess_risk(_ctx(("lib/billing.rb", 1)), _cfg(), root)
    assert result.unassessed == ["lib/billing.rb"]
    assert risk_segment(result) == "risk low (blast radius not assessed: `lib/billing.rb`)"


def test_a_failed_scan_marks_every_supported_file_unassessed(tmp_path: Path) -> None:
    """A timeout or error discards partial output; the other factors still apply."""
    root = _write(tmp_path, {"src/app.py": "", "infra/main.tf": ""})
    ctx = _ctx(("src/app.py", 1), ("infra/main.tf", 1))
    result = assess_risk(ctx, _cfg(), root, scanner=lambda *_: None)
    assert result.unassessed == ["src/app.py"]
    assert result.level == "high"


def test_no_supported_candidates_means_no_scan(tmp_path: Path) -> None:
    def boom(*_: object) -> str:
        raise AssertionError("scanned with nothing to measure")

    root = _write(tmp_path, {"lib/a.rb": ""})
    assess_risk(_ctx(("lib/a.rb", 1), ("README.md", 1)), _cfg(), root, scanner=boom)


def test_a_scanner_that_raises_makes_the_verdict_unavailable(tmp_path: Path) -> None:
    def boom(*_: object) -> str:
        raise RuntimeError("unexpected")

    root = _write(tmp_path, {"src/app.py": ""})
    result = assess_risk(_ctx(("src/app.py", 1)), _cfg(), root, scanner=boom)
    assert result.level is None
    assert risk_segment(result) == "risk unavailable"
    assert risk_marker(result) == ""


def test_the_default_scanner_runs_sandboxed_and_reports_failure_as_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 2, stdout="[]", stderr="bad")

    monkeypatch.setattr(risk_module.subprocess, "run", fake_run)
    assert risk_module.default_scanner("ast-grep", "rules", tmp_path) is None
    env = seen["env"]
    assert isinstance(env, dict) and "OPENAI_API_KEY" not in env
    assert seen["timeout"] == risk_module.SCAN_TIMEOUT_SECONDS
    assert seen["cwd"] == tmp_path

    def timeout_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(risk_module.subprocess, "run", timeout_run)
    assert risk_module.default_scanner("ast-grep", "rules", tmp_path) is None


# --- level rules ----------------------------------------------------------------


def test_a_core_module_and_infrastructure_together_are_critical(tmp_path: Path) -> None:
    root = _python_workspace(tmp_path, 30)
    result = assess_risk(_ctx(("src/pkg/core.py", 1), ("infra/main.tf", 3)), _cfg(), root)
    assert result.level == "critical"


def test_two_files_in_one_area_count_once(tmp_path: Path) -> None:
    result = assess_risk(_ctx(("infra/a.tf", 1), ("infra/b.tf", 1)), _cfg(), tmp_path)
    assert result.level == "high"
    assert _texts(result) == ["touches infrastructure: `infra/a.tf`, `infra/b.tf`"]


def test_security_plus_data_migration_is_critical(tmp_path: Path) -> None:
    ctx = _ctx(("api/auth/login.go", 1), ("migrations/0042_drop.sql", 1))
    assert assess_risk(ctx, _cfg(), tmp_path).level == "critical"


def test_a_medium_area_raises_to_medium(tmp_path: Path) -> None:
    result = assess_risk(_ctx(("pyproject.toml", 1)), _cfg(), tmp_path)
    assert result.level == "medium"
    assert _texts(result) == ["touches dependencies: `pyproject.toml`"]


def test_docs_and_tests_only_are_low(tmp_path: Path) -> None:
    ctx = _ctx(("README.md", 900), ("tests/test_auth.py", 80), ("docs/runbook.md", 5))
    result = assess_risk(ctx, _cfg(), tmp_path)
    assert result.level == "low"
    assert _texts(result) == ["docs and tests only"]


def test_test_and_doc_files_raise_no_area(tmp_path: Path) -> None:
    root = _write(tmp_path, {"app/view.py": ""})
    result = assess_risk(_ctx(("app/view.py", 1), ("tests/test_auth.py", 1)), _cfg(), root)
    assert result.level == "low"


def test_five_hundred_changed_lines_is_medium(tmp_path: Path) -> None:
    root = _write(tmp_path, {"app/view.py": ""})
    ctx = _ctx(("app/view.py", 480), ("tests/test_view.py", 20))
    result = assess_risk(ctx, _cfg(), root)
    assert result.level == "medium"
    assert _texts(result) == ["500 changed lines"]


def test_code_changed_without_tests_is_medium(tmp_path: Path) -> None:
    root = _write(tmp_path, {"app/view.py": ""})
    result = assess_risk(_ctx(("app/view.py", 60)), _cfg(), root)
    assert result.level == "medium"
    assert _texts(result) == ["60 changed lines of code with no test file changed"]


def test_a_small_untested_change_is_not_flagged(tmp_path: Path) -> None:
    root = _write(tmp_path, {"app/view.py": ""})
    result = assess_risk(_ctx(("app/view.py", 49)), _cfg(), root)
    assert result.level == "low"
    assert _texts(result) == ["no risk signals"]


def test_a_core_path_is_at_least_high(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "src/shared/__init__.py": "",
            "src/shared/clock.py": "",
            "src/a.py": "from shared import clock\n",
            "src/b.py": "import shared.clock\n",
        },
    )
    result = assess_risk(_ctx(("src/shared/clock.py", 1)), _cfg(core_paths=["src/shared/**"]), root)
    assert result.level == "high"
    assert "`src/shared/clock.py` is a configured core path, imported by 2 files" in _texts(result)


def test_reasons_rank_by_level_then_factor(tmp_path: Path) -> None:
    root = _python_workspace(tmp_path, 6)
    ctx = _ctx(("src/pkg/core.py", 60), ("infra/main.tf", 1))
    texts = _texts(assess_risk(ctx, _cfg(), root))
    assert texts[0].startswith("touches infrastructure")
    assert texts[1] == "`src/pkg/core.py` is imported by 6 files"


def test_backticks_in_paths_are_stripped(tmp_path: Path) -> None:
    result = assess_risk(_ctx(("infra/`x`.tf", 1)), _cfg(), tmp_path)
    assert _texts(result) == ["touches infrastructure: `infra/x.tf`"]


@pytest.mark.parametrize(
    ("path", "test", "doc"),
    [
        ("tests/test_api.py", True, False),
        ("pkg/test/helpers.go", True, False),
        ("web/__tests__/a.tsx", True, False),
        ("spec/models/user_spec.rb", True, False),
        ("src/test_utils.py", True, False),
        ("api/handler_test.go", True, False),
        ("web/a.test.ts", True, False),
        ("web/a.spec.js", True, False),
        ("src/contest.py", False, False),
        ("src/latest/app.py", False, False),
        ("README.md", False, True),
        ("docs/guide/index.rst", False, True),
        ("docs/diagram.png", False, True),
        ("notes.adoc", False, True),
        ("requirements.txt", False, False),
        ("src/docstring.py", False, False),
    ],
)
def test_test_and_doc_classification(path: str, test: bool, doc: bool) -> None:
    assert is_test_path(path) is test
    assert is_doc_path(path) is doc


# --- rendering ------------------------------------------------------------------


def _assessment(level: str | None, *texts: str, unassessed: list[str] | None = None):  # type: ignore[no-untyped-def]
    reasons = [
        RiskReason(factor="area", key=f"k{i}", level=level or "low", text=text)
        for i, text in enumerate(texts)
    ]
    return RiskAssessment(level=level, reasons=reasons, unassessed=unassessed or [])  # type: ignore[arg-type]


def test_segment_names_the_top_reason_and_counts_the_rest() -> None:
    risk = _assessment("high", "`a.py` is imported by 84 files", "touches cost: `x.tf`")
    assert risk_segment(risk) == "risk high (`a.py` is imported by 84 files, +1 more)"


def test_marker_names_the_level() -> None:
    assert risk_marker(_assessment("critical", "x")) == "<!-- lgtmaybe-risk:critical -->"


def test_section_lists_every_reason_and_the_unassessed_files() -> None:
    risk = _assessment("high", "one", "two", unassessed=["lib/a.rb"])
    assert render_risk_section(risk) == (
        "### **Risk of Change: High**\n\n- one\n- two\n- blast radius not assessed: `lib/a.rb`"
    )


def test_section_for_an_unavailable_assessment() -> None:
    assert render_risk_section(_assessment(None)) == (
        "### **Risk of Change: Unavailable**\n\n_The risk assessment could not run on this change._"
    )
