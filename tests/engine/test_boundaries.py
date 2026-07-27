"""Function/class boundary detection for context expansion (P4 remainder).

``definition_spans`` uses ast-grep (already a core dependency — the same tool
symbol resolution uses, never a second AST stack) to find the 1-based start
spans of function/class definitions in a file's head text, so hunk expansion
can pad up to the enclosing signature instead of a fixed line count. Best
effort throughout: an unknown language, a missing binary, or any ast-grep
failure returns [] and the caller keeps the fixed-line pad.
"""

from __future__ import annotations

import lgtmaybe.engine.boundaries as boundaries
from lgtmaybe.engine.boundaries import definition_spans

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


def test_python_definition_spans_found_with_real_ast_grep() -> None:
    spans = definition_spans(_PY, "src/sample.py")

    # def outer (6..12), class Thing (14..17), def method (15..17).
    assert [start for start, _ in spans] == [6, 14, 15]
    # Every span closes at or after it opens, and outer() ends before Thing.
    assert all(end >= start for start, end in spans)
    assert dict(spans)[6] < 14


def test_unknown_extension_returns_nothing() -> None:
    assert definition_spans("whatever", "notes.txt") == []


def test_missing_binary_returns_nothing() -> None:
    assert definition_spans(_PY, "src/sample.py", find_binary=lambda: None) == []


def test_runner_failure_returns_nothing() -> None:
    assert (
        definition_spans(_PY, "src/sample.py", runner=lambda binary, rule, path: "not json") == []
    )


def test_boundaries_temp_directory_ignores_cleanup_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}
    temporary_directory = boundaries.tempfile.TemporaryDirectory

    def recording_temp_directory(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return temporary_directory(*args, **kwargs)

    monkeypatch.setattr(boundaries.tempfile, "TemporaryDirectory", recording_temp_directory)

    definition_spans(
        _PY,
        "src/sample.py",
        runner=lambda *_args: "[]",
        find_binary=lambda: "ast-grep",
    )

    assert captured["ignore_cleanup_errors"] is True


# ---------------------------------------------------------------------------
# spans: a definition that has already ENDED does not enclose a later hunk
# ---------------------------------------------------------------------------

_PY_TRAILING_MODULE_CODE = """\
import os


def helper(a):
    x = a + 1
    y = x * 2
    return y


CONFIG = {
    "alpha": 1,
    "beta": 2,
}
"""


def test_definition_spans_report_where_a_definition_ends() -> None:
    """Only the start line was captured, so a caller could not tell whether a
    definition still contained a given line — ast-grep reports the end too."""
    spans = definition_spans(_PY_TRAILING_MODULE_CODE, "src/sample.py")

    assert spans, "expected at least the helper() definition"
    start, end = spans[0]
    assert start == 4  # def helper(a)
    # helper() ends at `return y` (line 7) — well before the module-level dict.
    assert end == 7


def test_module_level_code_is_enclosed_by_nothing() -> None:
    """A hunk on module-level code after a function is inside no definition."""
    from lgtmaybe.engine.compress import _enclosing_boundary

    spans = definition_spans(_PY_TRAILING_MODULE_CODE, "src/sample.py")

    # Line 12 is `"beta": 2,` in the module-level CONFIG dict.
    assert _enclosing_boundary(spans, 12) is None


def test_a_line_inside_a_definition_still_resolves() -> None:
    from lgtmaybe.engine.compress import _enclosing_boundary

    spans = definition_spans(_PY_TRAILING_MODULE_CODE, "src/sample.py")

    # Line 6 is `y = x * 2`, inside helper().
    assert _enclosing_boundary(spans, 6) == 4
