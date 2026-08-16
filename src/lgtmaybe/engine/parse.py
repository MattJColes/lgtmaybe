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
from collections.abc import Callable, Iterator
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel

from lgtmaybe.core.models import ReviewFinding

_M = TypeVar("_M", bound=BaseModel)


class ParseFailure(StrEnum):
    """Which fault produced an unparseable response.

    "It did not parse" is not a diagnosis: a model that answered in prose, one
    that meant to emit JSON and got the syntax wrong, and one that emitted clean
    JSON of a shape nobody asked for are three different problems with three
    different fixes. The parser is the only place that can still tell them
    apart — by the time the engine sees a ``ParseError`` the evidence is gone —
    so it says which, and the reason travels far enough that a run names its own
    cause without anyone re-running it with the raw body captured.

    A ``StrEnum`` because it rides into reason strings and JSON log fields,
    where ``ParseFailure.prose`` would read as a leaked repr.
    """

    empty = "empty"
    """The provider returned nothing at all."""
    prose = "prose"
    """No balanced JSON delimiter ever opened — the model ignored the ask."""
    malformed_json = "malformed_json"
    """JSON was attempted and does not decode."""
    not_findings = "not_findings"
    """Valid JSON that was never findings-shaped."""
    schema = "schema"
    """Findings-shaped, but rejected by the strict schema."""
    truncated = "truncated"
    """Cut off mid-JSON — the model ran out of output tokens."""


class ParseError(Exception):
    """Raised when the LLM response cannot be parsed into findings.

    ``shape`` names which fault it was (see ``ParseFailure``). ``truncated``
    predates it and remains the flag callers branch on — a response cut off
    mid-JSON is not a badly-behaved model, and the salvage below applies only to
    it — but it is now *derived* from the shape rather than tracked beside it,
    so the two can never disagree about the same failure.

    ``recovered`` carries the findings the model finished emitting before a
    truncation — real, schema-valid work worth posting. It rides on the error
    rather than being returned, because a caller must not be able to take the
    salvage without also seeing that the lens was cut short.

    The offending text is deliberately *not* carried here. Every caller parses
    text it already holds, so a ``raw`` field would hand back its own argument —
    and keeping the provider's body out of this module is what lets it stay
    free of logging, config and redaction concerns.
    """

    def __init__(
        self,
        message: str,
        *,
        shape: ParseFailure,
        recovered: list[ReviewFinding] | None = None,
    ) -> None:
        super().__init__(message)
        self.shape = shape
        self.recovered = recovered or []

    @property
    def truncated(self) -> bool:
        return self.shape is ParseFailure.truncated


# Regex to strip trailing commas before ] or }
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

# Reasoning blocks emitted by "thinking" models (qwen3.x etc.) — stripped before
# we look for JSON so their contents can't be mistaken for the answer.
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)

_CLOSER = {"{": "}", "[": "]"}


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> reasoning blocks."""
    return _THINK_RE.sub("", text)


def _outside_strings(text: str, start: int = 0) -> Iterator[tuple[int, str]]:
    """Yield ``(index, char)`` for each character of *text* outside a string literal.

    The single home of the quote/escape state machine this module sells as its
    whole point: a brace inside a ``suggestion`` is data, not nesting. Both
    delimiter walks below read it, so a fix to the invariant can no longer be a
    silent bug in the other one.
    """
    in_string = escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        else:
            yield i, ch


def _balanced_span(text: str, start: int) -> str | None:
    """Return the balanced ``{...}`` / ``[...]`` span beginning at *start*.

    Walks forward tracking string state (so quotes, escapes, and brackets inside
    string values don't affect nesting) and brace/bracket depth, returning the
    substring once depth returns to zero. ``None`` if the delimiter never closes.
    """
    opener = text[start]
    closer = _CLOSER[opener]
    depth = 0
    for i, ch in _outside_strings(text, start):
        if ch == opener:
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
        repaired = _TRAILING_COMMA_RE.sub(r"\1", candidate)
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


def _classify(raw: str) -> ParseFailure:
    """Which non-truncation fault *raw* is, told apart by how far it got.

    Cold path only — reached once a call has already failed, so re-walking the
    text costs nothing next to the model call that produced it. Written as its
    own walk rather than by instrumenting ``iter_json_values``, whose signature
    is shared with reflection, ``parse_structured`` and the truncation salvage;
    none of them want a second return value to answer a question only the
    failure path asks.

    The whole-text candidate ``iter_json_values`` tries first is deliberately
    *not* counted as an attempt at JSON: prose is text that does not decode, so
    counting it would make every prose response look like broken JSON. Only a
    balanced delimiter is evidence the model reached for a container at all —
    and only one carrying a quote, because the reason extraction scans for
    balanced spans in the first place is that prose is full of brackets that are
    not JSON (``reviewed 3 files [a, b, c]``). A findings payload cannot exist
    without a quoted key, so a span with no quote in it never was one, and
    calling it malformed would send the reader hunting a syntax bug in a model
    that never attempted the format.
    """
    text = _strip_think_blocks(raw).strip()
    if not text:
        return ParseFailure.empty
    # A complete scalar (``42``, ``true``, ``null``, ``"no findings"``) is valid
    # JSON with no container to find, so the span walk below would call it prose.
    # Checked first: reaching here at all means nothing findings-shaped
    # validated, so whatever this decodes to was never findings.
    try:
        json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return ParseFailure.not_findings
    spans = [
        span
        for i, ch in enumerate(text)
        if ch in _CLOSER and (span := _balanced_span(text, i)) is not None and '"' in span
    ]
    if not spans:
        return ParseFailure.prose
    for span in spans:
        try:
            json.loads(_TRAILING_COMMA_RE.sub(r"\1", span))
        except json.JSONDecodeError:
            continue
        # Something decoded, so the syntax was fine and the shape was not — the
        # findings-shaped candidates are already exhausted by the caller.
        return ParseFailure.not_findings
    return ParseFailure.malformed_json


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
    for _, ch in _outside_strings(text):
        if ch in _CLOSER:
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
        return data if all(isinstance(item, dict) for item in data) else None
    return None


def _is_container(value: Any) -> bool:
    """Whether *value* is a whole findings payload rather than one finding.

    The ``{"findings": [...]}`` envelope or a bare array — both of which a model
    can only emit once it has closed them, so seeing one means nothing was cut
    off. A lone object is the ambiguous case: the entire answer when the model
    found exactly one issue, or the first survivor of a truncated array.
    """
    if isinstance(value, list):
        return True
    return isinstance(value, dict) and isinstance(value.get("findings"), list)


def _recover_complete_findings(raw: str) -> list[ReviewFinding]:
    """Every valid finding the model finished emitting before it was cut off.

    Findings already generated, already paid for, and already validated against
    the strict schema — dropping them loses real signal, and a half-written
    trailing object simply fails validation and is left out rather than guessed
    at. Order follows the response, so the salvaged findings read as the model
    emitted them.
    """
    recovered: list[ReviewFinding] = []
    for value in iter_json_values(raw):
        if not isinstance(value, dict) or _is_container(value):
            continue
        try:
            recovered.append(ReviewFinding.model_validate(value))
        except Exception:  # noqa: S110 — a non-finding object is simply not one
            continue
    return recovered


def coerce_needs(value: object) -> list[str]:
    """Normalise a ``needs`` value into a clean list of non-empty path/symbol strings.

    Tolerates a caller that omits it (None), emits a single string, or includes
    blank/non-string entries — so a sloppy ``needs`` never raises, it just yields
    the paths worth fetching. Shared by the two places a model may defer: a
    reflection verdict (``reflect._parse_verdicts``) and a review lens's findings
    envelope (:func:`parse_needs`), so both read a deferral identically.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def parse_needs(raw: str) -> list[str]:
    """The ``needs`` deferral a review lens put on its findings envelope, else [].

    A lens that cannot decide without seeing code outside the diff answers
    ``{"findings": [...], "needs": [...]}`` — the paths (or symbols) it must
    read. Extraction is the same lenient walk :func:`parse_findings` uses, so a
    fenced or prose-wrapped answer still defers; the values go through
    :func:`coerce_needs`, so a malformed one degrades to "no deferral" rather
    than raising. ``parse_findings`` ignores the extra key, so the two are read
    independently from the one response.
    """
    for value in iter_json_values(raw):
        if isinstance(value, dict) and value.get("needs") is not None:
            return coerce_needs(value["needs"])
    return []


def parse_findings(raw: str) -> list[ReviewFinding]:
    """Parse *raw* LLM text into a list of ReviewFinding objects.

    Accepts the ``{"findings": [...]}`` structured-output envelope or a bare
    array, with reasoning blocks, fences, prose (even bracket-bearing prose), and
    trailing commas tolerated.

    Raises:
        ParseError: if the text cannot be recovered into valid findings.
    """
    if not raw or not raw.strip():
        raise ParseError("Empty response from provider", shape=ParseFailure.empty)

    # A findings-shaped candidate can still fail validation — e.g. a small model
    # emits a chatter object ({"note": "found 1 issue"}) before the real envelope,
    # and `_as_findings_list` treats any bare dict as a single finding. Keep trying
    # later candidates instead of aborting on the first that doesn't validate, so
    # the real `{"findings": [...]}` that follows is still recovered.
    last_error: Exception | None = None
    # A bare object is only the whole answer when nothing was cut off. In a
    # truncated array it is the FIRST of several complete findings, and returning
    # it here would report a fraction of the lens as the whole of it — silently,
    # with no notice, which is the one outcome worth more than the findings.
    # Held back until the truncation question is settled below.
    single: list[ReviewFinding] | None = None
    for value in iter_json_values(raw):
        items = _as_findings_list(value)
        if items is None:
            continue
        try:
            parsed = [ReviewFinding.model_validate(item) for item in items]
        except Exception as exc:
            last_error = exc
            continue
        if _is_container(value):
            return parsed
        single = single if single is not None else parsed

    # Checked BEFORE returning a bare object or reporting the validation error
    # below, both of which a truncated response also produces: a cut-off array
    # still holds complete earlier objects, and the first of those parses as a
    # bare single finding — validating (and masking the truncation) or failing
    # the schema (a symptom reported in place of the cause).
    if _is_unterminated(_strip_think_blocks(raw)):
        raise ParseError(
            "Response ended mid-JSON — the model ran out of output tokens",
            shape=ParseFailure.truncated,
            recovered=_recover_complete_findings(raw),
        )
    if single is not None:
        return single
    if last_error is not None:
        # Something WAS findings-shaped and the strict schema refused it, which
        # `_classify` cannot see: by its lights the payload decoded fine.
        raise ParseError(
            f"Finding validation failed: {last_error}", shape=ParseFailure.schema
        ) from last_error
    raise ParseError("Cannot parse JSON findings from response", shape=_classify(raw))


def parse_structured(
    raw: str, result_model: type[_M], wanted: Callable[[dict[str, Any]], bool]
) -> _M | None:
    """Leniently extract the first *wanted* JSON object from *raw*; None when absent."""
    for data in iter_json_values(raw):
        if not isinstance(data, dict) or not wanted(data):
            continue
        try:
            return result_model.model_validate(
                {k: v for k, v in data.items() if k in result_model.model_fields}
            )
        except Exception:  # noqa: BLE001 — fall through to the raw-text fallback
            continue
    return None
