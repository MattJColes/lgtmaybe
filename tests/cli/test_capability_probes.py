"""The CLI probes gateway capabilities through the ``Supports*`` protocols only.

A ``getattr(gateway, "method", None)`` probe turns a renamed adapter method into
a silently disabled feature that neither mypy nor any test notices. Probing with
``isinstance(gateway, SupportsX)`` and calling through the narrowed type keeps
the method names in one place (``core/ports.py``), where ``tests/test_ports.py``
checks every shipped gateway against them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import lgtmaybe.cli

CLI_DIR = Path(lgtmaybe.cli.__file__).parent

# Dynamic attribute lookups the CLI may make, keyed by the object looked up on.
# None of them is a gateway: platform signal names and an optional stream API.
ALLOWED_TARGETS = {"signal", "stream"}

_LOOKUPS = {"getattr", "hasattr"}


def _is_lookup(func: ast.expr) -> bool:
    """A bare ``getattr``/``hasattr``, or one qualified as ``builtins.getattr``.

    Any other attribute call (``obj.getattr(...)``) is someone's own method,
    not the builtin, so it is not a probe.
    """
    if isinstance(func, ast.Name):
        return func.id in _LOOKUPS
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _LOOKUPS
        and isinstance(func.value, ast.Name)
        and func.value.id == "builtins"
    )


def _lookups_in(tree: ast.AST) -> list[tuple[int, str]]:
    """Every builtin ``getattr``/``hasattr`` call in ``tree`` as (line, target)."""
    return [
        (node.lineno, ast.unparse(node.args[0]))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_lookup(node.func) and node.args
    ]


def _dynamic_lookups() -> list[tuple[str, int, str]]:
    """Every ``getattr``/``hasattr`` call in the CLI package as (file, line, target)."""
    found = []
    for path in sorted(CLI_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.extend((path.name, line, target) for line, target in _lookups_in(tree))
    return found


def _offending(lookups: list[tuple[int, str]]) -> list[str]:
    return [target for _line, target in lookups if target not in ALLOWED_TARGETS]


def test_the_scanner_reports_bare_qualified_and_hasattr_probes() -> None:
    tree = ast.parse(
        "import builtins\n"
        'getattr(gateway, "x")\n'
        'builtins.getattr(gateway, "x")\n'
        'hasattr(gateway, "x")\n'
    )
    assert _lookups_in(tree) == [(2, "gateway"), (3, "gateway"), (4, "gateway")]
    assert _offending(_lookups_in(tree)) == ["gateway", "gateway", "gateway"]


def test_the_scanner_ignores_methods_and_allowlisted_targets() -> None:
    tree = ast.parse('obj.getattr(gateway)\ngetattr(signal, "SIGTERM")\n')
    # A method named getattr is not the builtin, so it is not scanned at all.
    assert _lookups_in(tree) == [(2, "signal")]
    assert _offending(_lookups_in(tree)) == []


def test_the_scan_sees_the_cli_package() -> None:
    """Guard the guard: an empty walk would pass the test below vacuously."""
    assert any(target in ALLOWED_TARGETS for _file, _line, target in _dynamic_lookups())


def test_the_cli_never_probes_a_gateway_by_attribute_name() -> None:
    offending = [
        f"{file}:{line} getattr({target}, ...)"
        for file, line, target in _dynamic_lookups()
        if target not in ALLOWED_TARGETS
    ]
    assert not offending, (
        "probe gateway capabilities with isinstance(gateway, SupportsX) from "
        "lgtmaybe.core.ports, not by attribute name: " + ", ".join(offending)
    )
