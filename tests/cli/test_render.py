"""render_findings formats findings for the local CLI (human, json, agent)."""

from __future__ import annotations

import json

from lgtmaybe.cli import render_findings
from lgtmaybe.core.models import ReviewFinding, Severity

_FINDING = ReviewFinding(
    path="src/app.py",
    line=42,
    severity=Severity.high,
    title="possible NPE",
    body="`user` may be None here.",
    suggestion="if user is not None:",
)


def test_human_output_shows_location_severity_and_body() -> None:
    out = render_findings([_FINDING], "1 finding · llama3 · ~$0.00", fmt="human")

    assert "src/app.py:42" in out
    assert "[HIGH]" in out
    assert "possible NPE" in out
    assert "`user` may be None here." in out
    assert "if user is not None:" in out
    assert "1 finding · llama3 · ~$0.00" in out


def test_human_output_with_no_findings_is_just_the_summary() -> None:
    out = render_findings([], "👍 LGTM! · llama3 · ~$0.00", fmt="human")

    assert "👍 LGTM!" in out


def test_json_output_round_trips_to_findings() -> None:
    out = render_findings([_FINDING], "summary", fmt="json")

    parsed = json.loads(out)
    assert parsed == [_FINDING.model_dump(mode="json")]


def test_agent_output_is_directive_and_carries_the_fix() -> None:
    out = render_findings([_FINDING], "1 finding · llama3", fmt="agent")

    assert "apply" in out.lower()  # tells the AI to act, not just observe
    assert "src/app.py:42" in out
    assert "possible NPE" in out
    assert "`user` may be None here." in out
    assert "if user is not None:" in out


def test_agent_output_with_no_findings_says_nothing_to_correct() -> None:
    out = render_findings([], "👍 LGTM! · llama3", fmt="agent")

    assert "nothing to correct" in out.lower()


_NO_SUGGESTION = ReviewFinding(
    path="src/db.py",
    line=7,
    severity=Severity.low,
    title="unused import",
    body="`os` is never used.",
    suggestion=None,
)


def test_human_output_omits_suggestion_line_when_absent() -> None:
    out = render_findings([_NO_SUGGESTION], "summary", fmt="human")

    assert "unused import" in out
    assert "suggestion:" not in out


def test_agent_output_omits_suggested_fix_when_absent() -> None:
    out = render_findings([_NO_SUGGESTION], "summary", fmt="agent")

    assert "unused import" in out
    assert "Suggested fix:" not in out


def test_agent_output_indents_each_line_of_a_multiline_suggestion() -> None:
    finding = ReviewFinding(
        path="src/app.py",
        line=1,
        severity=Severity.medium,
        title="use a guard",
        body="add an early return",
        suggestion="if not user:\n    return None",
    )
    out = render_findings([finding], "summary", fmt="agent")

    assert "        if not user:" in out
    assert "            return None" in out


def test_human_output_renders_every_finding_with_one_trailing_summary() -> None:
    out = render_findings([_FINDING, _NO_SUGGESTION], "2 findings · llama3", fmt="human")

    assert "possible NPE" in out
    assert "unused import" in out
    assert out.count("2 findings · llama3") == 1
    assert out.rstrip().endswith("2 findings · llama3")


def test_human_format_shows_confidence_when_scored() -> None:
    finding = ReviewFinding(
        path="a.py",
        line=3,
        severity=Severity.high,
        title="real bug",
        body="broken",
        confidence=8,
    )

    out = render_findings([finding], "1 finding", fmt="human")

    assert "(confidence 8/10)" in out


def test_human_format_omits_confidence_when_unscored() -> None:
    finding = ReviewFinding(
        path="a.py", line=3, severity=Severity.high, title="real bug", body="broken"
    )

    out = render_findings([finding], "1 finding", fmt="human")

    assert "confidence" not in out
