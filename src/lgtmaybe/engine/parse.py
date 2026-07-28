"""JSON parse + repair for LLM review output.

Tolerates:
- The ``{"findings": [...]}`` structured-output envelope and a bare array alike
- ``<think>...</think>`` reasoning blocks (qwen-style models) before the JSON
- Markdown code fences (```json ... ```)
- Leading/trailing prose — even when it carries stray brackets
- Trailing commas in objects/arrays
- A bare object instead of an array

This matters most for ``openai-compatible`` gateways that don't honour the
``response_format`` JSON-mode hint (see issue #104): the model then answers in
conversational prose around the JSON, and that prose routinely contains brackets
(``"reviewed 3 files [a, b, c]"``). Extraction therefore scans for *balanced*
delimiters that respect JSON string literals, rather than greedily matching the
first ``[`` to the last ``]`` — and never rewrites the bytes inside a string,
so a code fence inside a ``suggestion`` survives intact.

Raises ParseError for unrecoverable input.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from lgtmaybe.core.models import ReviewFinding


class ParseError(Exception):
    """Raised when the LLM response cannot be parsed into findings.

    ``truncated`` distinguishes the two faults behind that: a response cut off
    mid-JSON (the model ran out of output tokens) versus one that is simply not
    findings JSON (prose, or a schema violation). They send a maintainer to
    different fixes, so the reviewer must not report them as the same thing.
    """

    def __init__(self, message: str, *, truncated: bool = False) -> None:
        super().__init__(message)
        self.truncated = truncated


# Regex to strip trailing commas before ] or }
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

# Reasoning blocks emitted by "thinking" models (qwen3.x etc.) — stripped before
# we look for JSON so their contents can't be mistaken for the answer.
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)

_CLOSER = {"{": "}", "[": "]"}


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> reasoning blocks."""
    return _THINK_RE.sub("", text)


def _repair_trailing_commas(text: str) -> str:
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def _balanced_span(text: str, start: int) -> str | None:
    """Return the balanced ``{...}`` / ``[...]`` span beginning at *start*.

    Walks forward tracking string state (so quotes, escapes, and brackets inside
    string values don't affect nesting) and brace/bracket depth, returning the
    substring once depth returns to zero. ``None`` if the delimiter never closes.
    """
    opener = text[start]
    closer = _CLOSER[opener]
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def iter_json_values(raw: str) -> Iterator[Any]:
    """Yield every JSON value recoverable from *raw*, most-likely first.

    Tries the whole think-stripped text first (clean JSON, no rewriting), then
    each balanced ``{``/``[`` span in order. Trailing commas are repaired before
    parsing. Spans that don't parse are skipped. The caller picks the value whose
    *shape* it wants, so embedded prose JSON (a stray ``["a", "b"]``) can be
    passed over in favour of the real payload.
    """
    text = _strip_think_blocks(raw).strip()
    if not text:
        return

    seen: set[str] = set()

    def _try(candidate: str) -> Iterator[Any]:
        repaired = _repair_trailing_commas(candidate)
        if repaired in seen:
            return
        seen.add(repaired)
        try:
            yield json.loads(repaired)
        except json.JSONDecodeError:
            return

    yield from _try(text)
    for i, ch in enumerate(text):
        if ch in _CLOSER:
            span = _balanced_span(text, i)
            if span is not None:
                yield from _try(span)


def _is_unterminated(text: str) -> bool:
    """True when *text* opens a JSON container it never closes.

    The provider-independent truncation signal, and the only one available when
    the route misreports why it stopped: litellm rewrites a finish reason it
    doesn't recognise to ``stop`` (OpenRouter answers ``error`` on a ceiling
    hit), so the response arrives looking clean and cut off in the same breath.

    String-aware, so a bracket inside a ``suggestion`` or a prose aside
    (``"reviewed [a, b"``) is data, not an unclosed container. Only consulted
    once parsing has already failed — a balanced payload followed by stray
    prose parses fine and never reaches here.
    """
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in _CLOSER:
            depth += 1
        elif ch in _CLOSER.values():
            depth -= 1
    return depth > 0


def _as_findings_list(data: Any) -> list[Any] | None:
    """Return *data* as a findings list if it is findings-shaped, else None.

    Accepts the ``{"findings": [...]}`` envelope, a bare object (a single
    finding), an empty list, or a list of objects. A list of non-objects (e.g. a
    stray ``["security", "perf"]`` from the prose) is *not* findings-shaped.
    """
    if isinstance(data, dict):
        findings = data.get("findings")
        if isinstance(findings, list):
            return findings
        return [data]  # a bare single finding object
    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            return data
    return None


def parse_findings(raw: str) -> list[ReviewFinding]:
    """Parse *raw* LLM text into a list of ReviewFinding objects.

    Accepts the ``{"findings": [...]}`` structured-output envelope or a bare
    array, with reasoning blocks, fences, prose (even bracket-bearing prose), and
    trailing commas tolerated.

    Raises:
        ParseError: if the text cannot be recovered into valid findings.
    """
    if not raw or not raw.strip():
        raise ParseError("Empty response from provider")

    # A findings-shaped candidate can still fail validation — e.g. a small model
    # emits a chatter object ({"note": "found 1 issue"}) before the real envelope,
    # and `_as_findings_list` treats any bare dict as a single finding. Keep trying
    # later candidates instead of aborting on the first that doesn't validate, so
    # the real `{"findings": [...]}` that follows is still recovered.
    last_error: Exception | None = None
    for value in iter_json_values(raw):
        items = _as_findings_list(value)
        if items is None:
            continue
        try:
            return [ReviewFinding.model_validate(item) for item in items]
        except Exception as exc:
            last_error = exc

    # Checked BEFORE the validation error below, which a truncated response can
    # also produce: a cut-off array still contains complete earlier objects, and
    # the first of those parses as a bare single finding and then fails schema
    # validation. That failure is a symptom — the root cause is the missing tail,
    # and reporting the symptom points at the wrong fix.
    if _is_unterminated(_strip_think_blocks(raw)):
        raise ParseError(
            "Response ended mid-JSON — the model ran out of output tokens", truncated=True
        )
    if last_error is not None:
        raise ParseError(f"Finding validation failed: {last_error}") from last_error
    raise ParseError("Cannot parse JSON findings from response")
