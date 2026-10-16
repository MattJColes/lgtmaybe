"""House style: the text lgtmaybe posts carries no em dash (or en dash).

The maintainer's style uses " - ", a colon, or a full stop where an em dash
would go. These tests render representative posted output (the change
overview's High Impact Areas in every state, finding bullets, the summary's
notices, the agent-format CLI output, a scan finding, a sequence diagram) and
guard the fixed strings in modules whose string literals are all user-facing.

Out of scope, deliberately: prompt text sent TO the model, log messages,
comments and docstrings. Model prose that happens to contain a dash is the
model's, not ours, so the fixtures here never put one in.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lgtmaybe.cli.render import _render_agent
from lgtmaybe.core.comment import finding_bullet, render_broad, render_demoted
from lgtmaybe.core.models import (
    DiagramNode,
    DiagramResult,
    DiagramStep,
    HighImpactArea,
    HighImpactResult,
    Provider,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.engine.diagram import render_diagram_views
from lgtmaybe.engine.engine import (
    _BUDGET_SKIP_REASON,
    _INTERRUPT_SKIP_REASON,
    _SCHEMA_DROP_NOTE,
    _build_notices,
    _NoticeState,
)
from lgtmaybe.engine.high_impact import render_high_impact
from lgtmaybe.engine.parse import ParseError, parse_findings
from lgtmaybe.engine.static_analysis import ToolFinding, tool_review_findings

_DASHES = ("—", "–")
_SRC = Path(__file__).resolve().parents[2] / "src" / "lgtmaybe"


def _assert_no_dash(text: str) -> None:
    for dash in _DASHES:
        assert dash not in text, f"{dash!r} in posted output:\n{text}"


def _finding(**overrides: object) -> ReviewFinding:
    fields: dict[str, object] = {
        "path": "src/app.py",
        "line": 3,
        "severity": Severity.high,
        "title": "Unchecked input",
        "body": "The value is used without a check.",
        "category": "security",
        "confidence": 8,
    }
    fields.update(overrides)
    return ReviewFinding.model_validate(fields)


class TestHighImpact:
    _CHANGED = ("infra/main.tf", "ops/backup_retention.yaml", "src/app.py")

    def test_model_areas_and_missed_signals(self) -> None:
        result = HighImpactResult(
            areas=[
                HighImpactArea(
                    area="infrastructure",
                    title="Cluster halved",
                    files=["infra/main.tf"],
                    why="Halving nodes removes headroom.",
                    check="Confirm peak load fits.",
                    severity="high",
                ),
                HighImpactArea(
                    area="availability",
                    title="New startup dependency",
                    files=["src/app.py"],
                    severity="medium",
                ),
            ],
        )
        rendered = render_high_impact(
            result,
            signals={"backup_and_recovery": ["ops/backup_retention.yaml"]},
            changed_files=self._CHANGED,
        )
        assert "not assessed by the model" in rendered
        _assert_no_dash(rendered)

    def test_model_unavailable(self) -> None:
        rendered = render_high_impact(
            None,
            signals={"infrastructure": ["infra/main.tf"]},
            changed_files=self._CHANGED,
        )
        assert "unavailable" in rendered
        _assert_no_dash(rendered)

    def test_none_detected(self) -> None:
        rendered = render_high_impact(
            HighImpactResult(areas=[]), signals={}, changed_files=self._CHANGED
        )
        assert "None detected" in rendered
        _assert_no_dash(rendered)


def test_finding_bullets_demoted_and_broad() -> None:
    finding = _finding()
    _assert_no_dash(finding_bullet(finding))
    _assert_no_dash(render_demoted([finding]))
    _assert_no_dash(render_broad([finding]))


def test_every_summary_notice() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="m", max_files=1, max_review_tokens=10)
    notices = _build_notices(
        _NoticeState(
            cfg=cfg,
            capped_files=True,
            total_files=2,
            oversized=["big.py"],
            skipped_by_triage=["dull.md"],
            errors=[_BUDGET_SKIP_REASON, _INTERRUPT_SKIP_REASON],
            total_calls=5,
            failed_calls=2,
            failed_lenses=["security"],
            split_batches=1,
            stepped_down=["security"],
            escalated={"security": "fallback-model"},
            repaired=["correctness"],
            re_asked=["architecture"],
            schema_dropped=True,
            reflection_skipped="Review interrupted (termination signal)",
            reflection_trimmed=True,
            flooded={"security": 3},
            suppressed=1,
            off_diff=1,
            open_finding_threads=1,
            self_talk={"security": 1},
            reasoning_off=["correctness"],
            spec_unfit=["openspec/changes/x"],
            spec_reviewed=False,
        )
    )
    assert len(notices) >= 15
    for notice in notices:
        _assert_no_dash(notice)
    for reason in (_BUDGET_SKIP_REASON, _INTERRUPT_SKIP_REASON, _SCHEMA_DROP_NOTE):
        _assert_no_dash(reason)


def test_agent_output_with_no_findings() -> None:
    _assert_no_dash(_render_agent([], "1 call, model m.", ""))


def test_scan_finding_body() -> None:
    [finding] = tool_review_findings(
        [
            ToolFinding(
                tool="ruff",
                path="src/app.py",
                line=1,
                rule="F401",
                message="unused import",
                severity=Severity.low,
            )
        ],
        {"src/app.py": "import os\n"},
    )
    _assert_no_dash(finding.body)


def test_sequence_step_without_a_label() -> None:
    diagram = DiagramResult(
        nodes=[DiagramNode(id="a", label="API"), DiagramNode(id="b", label="Cache")],
        steps=[DiagramStep(source="a", target="b")],
    )
    rendered = render_diagram_views(diagram, headed=True)
    assert rendered is not None
    _assert_no_dash(rendered)


def test_truncated_response_reason() -> None:
    with pytest.raises(ParseError) as caught:
        parse_findings('{"findings": [{"path": "a.py", "line": 1')
    _assert_no_dash(str(caught.value))


# Modules whose non-docstring, non-log string literals are all user-facing:
# posted comments, summaries and notices, CLI output, MCP tool output, and the
# exception messages a failure notice posts. Modules that also hold prompt text
# sent to the model are covered by the rendering tests above instead.
_OUTPUT_MODULES = (
    "azure_devops/gateway.py",
    "cli/__init__.py",
    "cli/mcp_server.py",
    "cli/render.py",
    "core/comment.py",
    "engine/engine.py",
    "engine/labels.py",
    "engine/overview.py",
    "engine/risk.py",
    "gitea/gateway.py",
    "github/rest_gateway.py",
    "gitlab/gateway.py",
    "providers/credentials.py",
    "providers/litellm_provider.py",
)
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})


def _excluded_string_ids(tree: ast.AST) -> set[int]:
    """Docstrings and every string inside a ``_log.<level>(...)`` call."""
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            excluded.add(id(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOG_METHODS
        ):
            excluded.update(id(inner) for inner in ast.walk(node))
    return excluded


@pytest.mark.parametrize("module", _OUTPUT_MODULES)
def test_output_module_literals_carry_no_dash(module: str) -> None:
    source = (_SRC / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    excluded = _excluded_string_ids(tree)
    offenders = [
        f"{module}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in excluded
        and any(dash in node.value for dash in _DASHES)
    ]
    assert not offenders, f"em/en dash in user-facing strings: {offenders}"
