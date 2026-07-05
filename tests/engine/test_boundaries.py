"""Function/class boundary detection for context expansion (P4 remainder).

``definition_starts`` uses ast-grep (already a core dependency — the same tool
symbol resolution uses, never a second AST stack) to find the 1-based start
lines of function/class definitions in a file's head text, so hunk expansion
can pad up to the enclosing signature instead of a fixed line count. Best
effort throughout: an unknown language, a missing binary, or any ast-grep
failure returns [] and the caller keeps the fixed-line pad.
"""

from __future__ import annotations

from lgtmaybe.engine.boundaries import definition_starts

_PY = """\
import os

CONST = 1


def outer(a, b):
    x = a + b
    if x > 0:
        for i in range(10):
            x += i
    return x


class Thing:
    def method(self):
        y = 1
        return y
"""


def test_python_definition_starts_found_with_real_ast_grep() -> None:
    starts = definition_starts(_PY, "src/sample.py")

    # def outer (line 6), class Thing (line 14), def method (line 15).
    assert starts == [6, 14, 15]


def test_unknown_extension_returns_nothing() -> None:
    assert definition_starts("whatever", "notes.txt") == []


def test_missing_binary_returns_nothing() -> None:
    assert definition_starts(_PY, "src/sample.py", find_binary=lambda: None) == []


def test_runner_failure_returns_nothing() -> None:
    assert (
        definition_starts(_PY, "src/sample.py", runner=lambda binary, rule, path: "not json") == []
    )
