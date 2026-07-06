"""Function/class boundary detection for context expansion (P4 remainder).

PR-Agent expands hunk context to the enclosing function rather than a fixed
line count; this module supplies the boundaries. ast-grep (already a core
dependency — the same binary symbol resolution uses, never a second AST
stack) parses the head file text and reports where function/class definitions
START, so ``compress.expand_hunks`` can pad the leading context up to the
enclosing signature when it sits above the fixed window.

Parsing, not executing: the text is written to a throwaway temp file and
structurally scanned, the same fork-safety posture as symbol resolution and
static analysis. Best-effort throughout — an unsupported language, a missing
binary, or any ast-grep failure returns ``[]`` and the caller keeps the plain
fixed-line pad.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from lgtmaybe.core.logging import get_logger

from .astgrep import _find_binary

_log = get_logger(__name__)

# One file parse is tens of milliseconds; the timeout only caps pathology.
_SCAN_TIMEOUT = 10

# Abstracts the ast-grep subprocess: (binary, rule_yaml, file) -> stdout ("" on
# failure). Injected so tests don't have to shell out.
BoundaryRunner = Callable[[str, str, Path], str]

# Extension -> (ast-grep language, block-level definition kinds). Deliberately a
# narrower table than symbol resolution's: only kinds that OPEN a body worth
# padding to (functions/methods/classes) — a variable_declarator boundary would
# be noise. An extension not listed simply keeps the fixed-line pad.
_LANG_DEFS: dict[str, tuple[str, tuple[str, ...]]] = {
    ".py": ("python", ("function_definition", "class_definition")),
    ".js": ("javascript", ("function_declaration", "method_definition", "class_declaration")),
    ".jsx": ("javascript", ("function_declaration", "method_definition", "class_declaration")),
    ".ts": (
        "typescript",
        ("function_declaration", "method_definition", "class_declaration"),
    ),
    ".tsx": ("tsx", ("function_declaration", "method_definition", "class_declaration")),
    ".go": ("go", ("function_declaration", "method_declaration")),
    ".rs": ("rust", ("function_item", "impl_item", "trait_item")),
    ".java": ("java", ("method_declaration", "class_declaration")),
    ".rb": ("ruby", ("method", "singleton_method", "class", "module")),
}


def definition_starts(
    text: str,
    path: str,
    *,
    runner: BoundaryRunner | None = None,
    find_binary: Callable[[], str | None] = _find_binary,
) -> list[int]:
    """Sorted 1-based start lines of function/class definitions in *text*.

    ``[]`` whenever boundaries can't be found cheaply and safely: unsupported
    extension, no ast-grep binary, or any scan/parse failure — the caller then
    pads by fixed line count exactly as before.
    """
    lang_defs = _LANG_DEFS.get(Path(path).suffix.lower())
    if lang_defs is None:
        return []
    binary = find_binary()
    if binary is None:
        return []
    language, kinds = lang_defs
    kinds_flow = ", ".join(f"{{kind: {k}}}" for k in kinds)
    rule = f"id: lgtmaybe-boundaries\nlanguage: {language}\nrule: {{any: [{kinds_flow}]}}\n"
    run = runner if runner is not None else _default_runner
    with tempfile.TemporaryDirectory(prefix="lgtmaybe-bounds-") as tmp:
        target = Path(tmp) / f"file{Path(path).suffix.lower()}"
        target.write_text(text)
        stdout = run(binary, rule, target)
    return _parse_starts(stdout)


def _default_runner(binary: str, rule_yaml: str, target: Path) -> str:
    try:
        proc = subprocess.run(
            [binary, "scan", "--inline-rules", rule_yaml, "--json=compact", str(target)],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout or ""


def _parse_starts(stdout: str) -> list[int]:
    """1-based, de-duplicated, sorted start lines from ast-grep's match array."""
    try:
        data = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    starts: set[int] = set()
    for match in data:
        if not isinstance(match, dict):
            continue
        line = match.get("range", {}).get("start", {}).get("line")
        if isinstance(line, int) and line >= 0:
            starts.add(line + 1)  # ast-grep lines are 0-based
    return sorted(starts)
