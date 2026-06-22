"""Tests for parse.py — JSON parse + repair."""

from __future__ import annotations

import json

import pytest

from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.engine.parse import ParseError, parse_findings

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_VALID_FINDING = {
    "path": "src/app.py",
    "line": 10,
    "side": "RIGHT",
    "severity": "high",
    "title": "null deref",
    "body": "may be None",
    "suggestion": None,
}


def _json_findings(findings: list[dict]) -> str:  # type: ignore[type-arg]
    import json

    return json.dumps(findings)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_plain_json_array_parses() -> None:
    raw = _json_findings([_VALID_FINDING])
    result = parse_findings(raw)
    assert len(result) == 1
    assert isinstance(result[0], ReviewFinding)
    assert result[0].severity == Severity.high


def test_markdown_fence_stripped() -> None:
    raw = "```json\n" + _json_findings([_VALID_FINDING]) + "\n```"
    result = parse_findings(raw)
    assert len(result) == 1


def test_prose_wrapped_json_extracted() -> None:
    raw = "Here are my findings:\n\n" + _json_findings([_VALID_FINDING]) + "\n\nHope that helps!"
    result = parse_findings(raw)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# gateway output without JSON mode (issue #104): conversational prose that
# carries stray brackets, and JSON whose own string values contain code fences
# ---------------------------------------------------------------------------


def test_prose_with_brackets_before_json() -> None:
    """Conversational lead-in with brackets must not derail extraction."""
    raw = "I reviewed 3 files [app.py, db.py, util.py] and found:\n" + _json_findings(
        [_VALID_FINDING]
    )
    result = parse_findings(raw)
    assert len(result) == 1
    assert result[0].path == "src/app.py"


def test_valid_json_array_in_prose_before_findings_is_skipped() -> None:
    """A real JSON array in the prose (e.g. a category list) is not mistaken
    for the findings — the findings-shaped candidate is selected instead."""
    raw = 'Categories checked: ["security", "perf"].\n' + json.dumps({"findings": [_VALID_FINDING]})
    result = parse_findings(raw)
    assert len(result) == 1
    assert result[0].severity == Severity.high


def test_leading_non_finding_object_is_skipped() -> None:
    """A small model that emits a chatter object before the real envelope must
    not abort parsing — any bare dict is treated as a candidate finding, so the
    parser has to keep going past one that fails validation."""
    raw = '{"note": "found 1 issue"}\n' + json.dumps({"findings": [_VALID_FINDING]})
    result = parse_findings(raw)
    assert len(result) == 1
    assert result[0].path == "src/app.py"


def test_only_non_finding_object_raises_with_validation_detail() -> None:
    """When nothing findings-shaped validates, the error names the validation
    failure (not a generic 'cannot parse'), so the cause is debuggable."""
    with pytest.raises(ParseError, match="Finding validation failed"):
        parse_findings('{"note": "no findings here"}')


def test_trailing_prose_with_bracket() -> None:
    """A closing remark with a bracket after the JSON must not derail extraction."""
    raw = json.dumps({"findings": [_VALID_FINDING]}) + "\nThat is all [done]."
    result = parse_findings(raw)
    assert len(result) == 1


def test_fenced_json_with_prose_on_both_sides() -> None:
    raw = (
        "Sure! Here are the issues I found:\n\n```json\n"
        + json.dumps({"findings": [_VALID_FINDING]})
        + "\n```\n\nLet me know if you need more detail."
    )
    result = parse_findings(raw)
    assert len(result) == 1


def test_suggestion_code_fence_survives_verbatim() -> None:
    """A code fence inside a string value must not be stripped — the old global
    backtick removal silently corrupted suggestions that contained code blocks."""
    suggestion = "Use the stdlib instead:\n```python\nimport json\n```"
    finding = dict(_VALID_FINDING, suggestion=suggestion)
    raw = json.dumps({"findings": [finding]})
    result = parse_findings(raw)
    assert len(result) == 1
    assert result[0].suggestion == suggestion


def test_trailing_comma_tolerated() -> None:
    raw = '[{"path":"a.py","line":1,"severity":"low","title":"t","body":"b","suggestion":null,}]'
    result = parse_findings(raw)
    assert len(result) == 1


def test_multiple_findings_parse() -> None:
    finding2 = dict(_VALID_FINDING, line=20, severity="medium", title="other")
    raw = _json_findings([_VALID_FINDING, finding2])
    result = parse_findings(raw)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# structured-output envelope + reasoning blocks
# ---------------------------------------------------------------------------


def test_findings_envelope_object_parses() -> None:
    """The structured-output shape: {"findings": [...]}"""
    import json

    raw = json.dumps({"findings": [_VALID_FINDING]})
    result = parse_findings(raw)
    assert len(result) == 1
    assert isinstance(result[0], ReviewFinding)


def test_empty_findings_envelope_parses_to_empty() -> None:
    assert parse_findings('{"findings": []}') == []


def test_think_block_stripped_before_json() -> None:
    """qwen-style <think> reasoning (which may contain brackets) is removed first."""
    import json

    raw = (
        "<think>Let me look... there might be an array like [1, 2, 3] in here</think>\n"
        + json.dumps({"findings": [_VALID_FINDING]})
    )
    result = parse_findings(raw)
    assert len(result) == 1


def test_think_block_then_fenced_envelope() -> None:
    import json

    raw = (
        "<think>reasoning</think>\n```json\n" + json.dumps({"findings": [_VALID_FINDING]}) + "\n```"
    )
    assert len(parse_findings(raw)) == 1


# ---------------------------------------------------------------------------
# malformed-but-recoverable
# ---------------------------------------------------------------------------


def test_single_object_wrapped_in_list() -> None:
    """Model returns a bare object instead of an array."""
    import json

    raw = json.dumps(_VALID_FINDING)
    result = parse_findings(raw)
    assert len(result) == 1


def test_extra_whitespace_and_newlines() -> None:
    raw = "\n\n  " + _json_findings([_VALID_FINDING]) + "  \n\n"
    result = parse_findings(raw)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# unrecoverable
# ---------------------------------------------------------------------------


def test_pure_garbage_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_findings("this is not json at all, just prose, no brackets")


def test_empty_string_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_findings("")


def test_whitespace_only_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_findings("   \n\t  ")


# ---------------------------------------------------------------------------
# schema enforcement — the model output is untrusted; reject drift loudly
# ---------------------------------------------------------------------------


def test_empty_array_yields_no_findings() -> None:
    """A clean review (empty array) is valid and parses to zero findings."""
    assert parse_findings("[]") == []


def test_unknown_field_is_rejected() -> None:
    """`extra=forbid` on the model means injected/extra keys fail, not slip through."""
    bad = dict(_VALID_FINDING, exploit="rm -rf /")
    with pytest.raises(ParseError):
        parse_findings(_json_findings([bad]))


def test_invalid_severity_is_rejected() -> None:
    bad = dict(_VALID_FINDING, severity="catastrophic")
    with pytest.raises(ParseError):
        parse_findings(_json_findings([bad]))


def test_missing_required_field_is_rejected() -> None:
    bad = {k: v for k, v in _VALID_FINDING.items() if k != "body"}
    with pytest.raises(ParseError):
        parse_findings(_json_findings([bad]))


def test_non_integer_line_is_rejected() -> None:
    bad = dict(_VALID_FINDING, line="not-a-number")
    with pytest.raises(ParseError):
        parse_findings(_json_findings([bad]))


def test_json_null_literal_raises_parse_error() -> None:
    """A literal `null` is neither an array nor an object of findings."""
    with pytest.raises(ParseError):
        parse_findings("null")
