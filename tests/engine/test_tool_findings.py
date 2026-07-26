"""Direct-post mode: turning deterministic tool output into review findings.

A `hint`-mode tool grounds the model (`format_hints`); a `finding`-mode tool
skips the model entirely and posts. That second path has no model in it to
filter noise or strip a secret, so the mapping carries the whole contract:

- the finding is namespaced to its tool, so `finding_rules` can target it and
  the engine's defect-evidence gate does not apply;
- `title` is deterministic, so `finding_fingerprint` is stable run to run and
  the ignore / feedback channels keep working across re-runs;
- `anchor` is the corpus line, redacted to match the redacted diff the engine
  snaps against — a line that does not exist is dropped, never guessed;
- a raw secret never reaches a `ReviewFinding` field, whatever the tool emitted.
"""

from __future__ import annotations

from lgtmaybe.core.models import (
    Provider,
    ReviewConfig,
    Severity,
    StaticAnalysisTool,
    ToolMode,
)
from lgtmaybe.engine.redact import redact
from lgtmaybe.engine.static_analysis import (
    MAX_SCAN_FINDINGS,
    ToolFinding,
    mode_for,
    tool_review_findings,
)

CORPUS = {"src/app.py": "import os\nSECRET = 'AKIAIOSFODNN7EXAMPLE'\nx = 1\n"}


def _finding(**overrides: object) -> ToolFinding:
    base = {
        "tool": "gitleaks",
        "path": "src/app.py",
        "line": 2,
        "rule": "aws-access-key-id",
        "message": "AWS access key detected",
        "severity": Severity.high,
    }
    return ToolFinding(**{**base, **overrides})  # type: ignore[arg-type]


def test_tool_finding_becomes_a_postable_review_finding() -> None:
    (finding,) = tool_review_findings([_finding()], CORPUS)

    assert finding.path == "src/app.py"
    assert finding.line == 2
    assert finding.side == "RIGHT"
    assert finding.severity is Severity.high
    assert "aws-access-key-id" in finding.title
    assert "AWS access key detected" in finding.body


def test_category_is_namespaced_per_tool() -> None:
    """`scan:<tool>` keeps rules targetable and sidesteps the defect-evidence gate.

    The engine drops built-in defect findings (security/correctness/deprecation/
    performance) that carry no `failure_scenario`. A scan finding has no causal
    story to tell, so it must not land in one of those categories.
    """
    (finding,) = tool_review_findings([_finding()], CORPUS)

    assert finding.category == "scan:gitleaks"
    assert finding.failure_scenario is None


def test_title_is_deterministic_so_fingerprints_survive_a_rerun() -> None:
    first = tool_review_findings([_finding()], CORPUS)[0]
    second = tool_review_findings([_finding()], CORPUS)[0]

    assert first.title == second.title


def test_anchor_is_the_corpus_line() -> None:
    """The engine re-anchors on this text, so it must match the source line."""
    (finding,) = tool_review_findings([_finding(line=3)], CORPUS)

    assert finding.anchor == "x = 1"


def test_anchor_is_redacted_so_it_matches_the_redacted_diff() -> None:
    """Not just safety — anchoring does not work without it.

    The engine snaps findings against `redact(ctx.diff)`, so an anchor taken raw
    from a credential-bearing line would match nothing and the finding would be
    dropped as unanchored. Redaction is line-stable, so the redacted anchor and
    the redacted diff line agree.
    """
    (finding,) = tool_review_findings([_finding(line=2)], CORPUS)

    assert finding.anchor is not None
    assert "AKIAIOSFODNN7EXAMPLE" not in finding.anchor
    assert finding.anchor == redact(CORPUS["src/app.py"].splitlines()[1])


def test_line_past_the_end_of_the_file_is_dropped_not_guessed() -> None:
    assert tool_review_findings([_finding(line=99)], CORPUS) == []


def test_finding_for_a_file_with_no_corpus_text_is_dropped() -> None:
    assert tool_review_findings([_finding(path="other.py")], CORPUS) == []


def test_findings_are_capped_most_severe_first() -> None:
    """Direct posts bypass the hint cap; without one, a noisy pack floods the PR."""
    lows = [_finding(rule=f"low-{i}", severity=Severity.low) for i in range(MAX_SCAN_FINDINGS)]
    criticals = [_finding(rule="crit", severity=Severity.critical)]

    kept = tool_review_findings(lows + criticals, CORPUS)

    assert len(kept) == MAX_SCAN_FINDINGS
    assert kept[0].severity is Severity.critical


def test_a_secret_in_tool_output_never_reaches_the_finding() -> None:
    """gitleaks can emit the matched secret; nothing it emits may be posted.

    Belt and braces over `--redact`: even if a tool hands us a live credential
    in its message — or in the source line it points at — redaction must scrub it
    before it becomes a finding. The check is on the whole serialised model, so a
    future field cannot quietly open a new leak path.
    """
    leaky = _finding(message="found AKIAIOSFODNN7EXAMPLE in config")

    (finding,) = tool_review_findings([leaky], CORPUS)

    assert "AKIAIOSFODNN7EXAMPLE" not in finding.model_dump_json()


def test_deterministic_tools_default_to_posting_and_interpretive_ones_to_hinting() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")

    assert mode_for(StaticAnalysisTool.gitleaks, cfg) is ToolMode.finding
    assert mode_for(StaticAnalysisTool.ruff, cfg) is ToolMode.hint


def test_tool_mode_overrides_the_built_in_default() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    sa = cfg.static_analysis.model_copy(
        update={"tool_mode": {StaticAnalysisTool.gitleaks: ToolMode.hint}}
    )
    cfg = cfg.model_copy(update={"static_analysis": sa})

    assert mode_for(StaticAnalysisTool.gitleaks, cfg) is ToolMode.hint


def test_a_custom_lens_cannot_impersonate_a_scan_tool() -> None:
    """The engine keys "is this deterministic?" on the category prefix.

    A lens id starting with `scan:` would make the model's findings skip
    reflection and be dropped when unanchored — two behaviours reserved for
    tools. Reject the id rather than let a lens quietly inherit them.
    """
    import pytest
    from pydantic import ValidationError

    from lgtmaybe.core.models import CustomLens

    with pytest.raises(ValidationError, match="scan:"):
        CustomLens(id="scan:gitleaks", instructions="pretend to be a tool")
