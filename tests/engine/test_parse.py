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
# truncation — a response cut off mid-JSON is a different fault from prose, and
# the reviewer must say which. Detected from the text itself because a provider
# can misreport it: OpenRouter answered `finish_reason: 'error'` on a run that
# hit the output ceiling, and litellm maps an unknown reason to 'stop'.
# ---------------------------------------------------------------------------


def test_truncated_findings_array_is_flagged_as_truncated() -> None:
    """The response ran out of output tokens mid-array: the envelope opens and
    never closes, so no balanced span exists to parse."""
    raw = '{"findings": [{"path": "a.py", "line": 1, "side": "RIGHT", "severity": "high", "ti'
    with pytest.raises(ParseError) as exc_info:
        parse_findings(raw)
    assert exc_info.value.truncated is True


def test_truncated_bare_array_is_flagged_as_truncated() -> None:
    raw = '[{"path": "a.py", "line": 1}, {"path": "b.py"'
    with pytest.raises(ParseError) as exc_info:
        parse_findings(raw)
    assert exc_info.value.truncated is True


def test_prose_without_json_is_not_flagged_as_truncated() -> None:
    """A model that answered in prose is a different fault — reporting it as a
    truncation would send the user to the wrong knob."""
    with pytest.raises(ParseError) as exc_info:
        parse_findings("this is not json at all, just prose, no brackets")
    assert exc_info.value.truncated is False


def test_complete_but_invalid_json_is_not_flagged_as_truncated() -> None:
    """Balanced delimiters that fail validation are malformed, not cut off."""
    with pytest.raises(ParseError) as exc_info:
        parse_findings('{"findings": [{"path": "a.py", "severity": "nope"}]}')
    assert exc_info.value.truncated is False


def _truncated_after(findings: list[dict], tail: str) -> str:  # type: ignore[type-arg]
    """A ``{"findings": [...]}`` envelope cut off partway through *tail*."""
    complete = ", ".join(json.dumps(f) for f in findings)
    return f'{{"findings": [{complete}, {tail}'


def test_complete_findings_before_the_cut_are_recovered() -> None:
    """The findings the model finished emitting are real work, already validated
    — throwing them away loses genuine signal the run has already paid for."""
    second = {**_VALID_FINDING, "path": "src/other.py", "title": "off by one"}
    raw = _truncated_after([_VALID_FINDING, second], '{"path": "src/third.py", "li')

    with pytest.raises(ParseError) as exc_info:
        parse_findings(raw)

    recovered = exc_info.value.recovered
    assert [f.path for f in recovered] == ["src/app.py", "src/other.py"]
    assert all(isinstance(f, ReviewFinding) for f in recovered)
    assert recovered[1].title == "off by one"


def test_the_incomplete_finding_itself_is_not_recovered() -> None:
    """Only whole objects — a half-written finding is not silently completed."""
    raw = _truncated_after([_VALID_FINDING], '{"path": "src/third.py", "severity": "hi')

    with pytest.raises(ParseError) as exc_info:
        parse_findings(raw)

    assert [f.path for f in exc_info.value.recovered] == ["src/app.py"]


def test_truncation_before_any_complete_finding_recovers_nothing() -> None:
    with pytest.raises(ParseError) as exc_info:
        parse_findings('{"findings": [{"path": "src/app.py", "li')

    assert exc_info.value.recovered == []


def test_a_non_truncated_parse_error_recovers_nothing() -> None:
    """Recovery is the truncation path only; prose has nothing to salvage."""
    with pytest.raises(ParseError) as exc_info:
        parse_findings("no json here at all")

    assert exc_info.value.recovered == []


def test_recovery_skips_objects_that_are_not_findings() -> None:
    """A chatter object before the envelope is not a finding, and strict
    validation is what keeps it out — recovery never widens the schema."""
    raw = '{"note": "found 1 issue"} {"findings": [' + json.dumps(_VALID_FINDING) + ', {"path": "x'

    with pytest.raises(ParseError) as exc_info:
        parse_findings(raw)

    assert [f.path for f in exc_info.value.recovered] == ["src/app.py"]


def test_string_containing_an_unclosed_bracket_is_not_truncated() -> None:
    """A bracket inside a JSON string is data, not an unterminated container."""
    with pytest.raises(ParseError) as exc_info:
        parse_findings('{"note": "reviewed [a, b"}')
    assert exc_info.value.truncated is False


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


def test_mixed_case_severity_does_not_drop_sibling_findings() -> None:
    """A capitalised severity must coerce, not fail and lose its batch siblings.

    The batch is validated in one comprehension, so before severity coercion a
    single "High" item raised and the parser recovered only findings that happened
    to parse as standalone objects — silently losing the rest.
    """
    miscased = dict(_VALID_FINDING, path="a.py", severity="High")
    valid = dict(_VALID_FINDING, path="b.py", severity="critical")
    result = parse_findings(_json_findings([miscased, valid]))
    assert {f.path for f in result} == {"a.py", "b.py"}
    assert result[0].severity == Severity.high
