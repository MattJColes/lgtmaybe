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


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _dynamic_lookups() -> list[tuple[str, int, str]]:
    """Every ``getattr``/``hasattr`` call in the CLI package as (file, line, target)."""
    found = []
    for path in sorted(CLI_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # A bare call or a qualified one (``builtins.getattr``).
            if not (
                isinstance(node, ast.Call)
                and _callee(node.func) in {"getattr", "hasattr"}
                and node.args
            ):
                continue
            target = ast.unparse(node.args[0])
            found.append((path.name, node.lineno, target))
    return found


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
