"""Function/class boundary detection for context expansion (P4 remainder).

PR-Agent expands hunk context to the enclosing function rather than a fixed
line count; this module supplies the boundaries. ast-grep (already a core
dependency — the same binary symbol resolution uses, never a second AST
stack) parses the head file text and reports where function/class definitions
START, so ``compress.expand_hunks`` can pad the leading context up to the
enclosing signature when it sits above the fixed window.

The language table is astgrep.py's single one: this module asks it for the
``block`` kinds — the ones that OPEN a body worth padding to (functions, methods,
classes), never a ``variable_declarator`` — and an extension it doesn't list
simply keeps the fixed-line pad.

Parsing, not executing: the text is written to a throwaway temp file and
structurally scanned, the same fork-safety posture as symbol resolution and
static analysis. Best-effort throughout — an unsupported language, a missing
binary, or any ast-grep failure returns ``[]`` and the caller keeps the plain
fixed-line pad.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from lgtmaybe.core.logging import get_logger

from .astgrep import _default_runner, _find_binary, block_kinds_for_path, iter_matches, rule_yaml

_log = get_logger(__name__)

# Abstracts the ast-grep subprocess: (binary, rule_yaml, file) -> stdout ("" on
# failure). Injected so tests don't have to shell out.
BoundaryRunner = Callable[[str, str, Path], str]


def definition_spans(
    text: str,
    path: str,
    *,
    runner: BoundaryRunner | None = None,
    find_binary: Callable[[], str | None] = _find_binary,
) -> list[tuple[int, int]]:
    """Sorted 1-based ``(start, end)`` lines of definitions in *text*, inclusive.

    The END matters as much as the start: a definition that has already closed
    does not enclose a later line. Without it, module-level code sitting after a
    function looked "enclosed" by that function, and the caller padded a hunk
    back into an unrelated body.

    ``[]`` whenever boundaries can't be found cheaply and safely: unsupported
    extension, no ast-grep binary, or any scan/parse failure — the caller then
    pads by fixed line count exactly as before.
    """
    lang_defs = block_kinds_for_path(path)
    if lang_defs is None:
        return []
    binary = find_binary()
    if binary is None:
        return []
    rule = rule_yaml(*lang_defs)
    run = runner if runner is not None else _default_runner
    with tempfile.TemporaryDirectory(prefix="lgtmaybe-bounds-", ignore_cleanup_errors=True) as tmp:
        target = Path(tmp) / f"file{Path(path).suffix.lower()}"
        target.write_text(text, encoding="utf-8")
        stdout = run(binary, rule, target)
    return _parse_spans(stdout)


def _parse_spans(stdout: str) -> list[tuple[int, int]]:
    """1-based, de-duplicated, sorted ``(start, end)`` spans from ast-grep's matches.

    A match whose range is missing, malformed, or inverted is dropped rather
    than guessed at — a wrong span would pad a hunk to the wrong place.
    """
    spans: set[tuple[int, int]] = set()
    for match in iter_matches(stdout):
        span = match.get("range", {})
        start = span.get("start", {}).get("line")
        end = span.get("end", {}).get("line")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
            spans.add((start + 1, end + 1))  # ast-grep lines are 0-based
    return sorted(spans)
