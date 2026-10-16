"""Every hidden marker is written next to the regex that reads it back.

A marker written by hand in an adapter and parsed by a regex in ``core`` can
drift apart silently: the adapter stamps one shape, the reader looks for
another, and incremental review quietly falls back to a full review forever.
So each marker family has one writer, beside its reader, and these tests pin
the round trip. A gateway never spells a marker out itself.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import lgtmaybe
from lgtmaybe.core.comment import (
    DIAGRAMMED_MARKER,
    FINDING_MARKER,
    IDENTITY_MARKER,
    REVIEWED_DIFF_MARKER,
    REVIEWED_MARKER,
    diagrammed_marker,
    finding_keys,
    last_match,
    render_inline_body,
    retire_stamped_ids,
    reviewed_diff_marker,
    reviewed_marker,
    split_stamped_ids,
    stamped_ids,
)
from lgtmaybe.core.diff import diff_fingerprint
from lgtmaybe.core.findings import finding_fingerprint, finding_identity
from lgtmaybe.core.models import ReviewFinding, Severity

_SHAS = ["abc1234", "0123456789abcdef0123456789abcdef01234567"]


@pytest.mark.parametrize("sha", _SHAS)
def test_reviewed_marker_round_trips(sha: str) -> None:
    written = reviewed_marker(sha)

    assert REVIEWED_MARKER.fullmatch(written)
    assert last_match(REVIEWED_MARKER, f"summary\n{written}") == sha


@pytest.mark.parametrize("sha", _SHAS)
def test_diagrammed_marker_round_trips(sha: str) -> None:
    written = diagrammed_marker(sha)

    assert DIAGRAMMED_MARKER.fullmatch(written)
    assert last_match(DIAGRAMMED_MARKER, f"diagram\n{written}") == sha


def test_reviewed_diff_marker_round_trips() -> None:
    fingerprint = diff_fingerprint("diff --git a/x b/x\n+added\n")
    written = reviewed_diff_marker(fingerprint)

    assert REVIEWED_DIFF_MARKER.fullmatch(written)
    assert last_match(REVIEWED_DIFF_MARKER, f"summary\n{written}") == fingerprint


def test_watermark_families_are_disjoint() -> None:
    """One family's writer is never read back as another family's marker."""
    sha = "abc1234"
    fingerprint = diff_fingerprint("diff --git a/x b/x\n+added\n")
    writers = {
        REVIEWED_MARKER: reviewed_marker(sha),
        DIAGRAMMED_MARKER: diagrammed_marker(sha),
        REVIEWED_DIFF_MARKER: reviewed_diff_marker(fingerprint),
    }
    for reader in writers:
        for owner, written in writers.items():
            if owner is not reader:
                assert reader.search(written) is None, (reader.pattern, written)


def _finding() -> ReviewFinding:
    return ReviewFinding(
        path="src/app.py",
        line=2,
        severity=Severity.high,
        title="Title",
        body="Body",
        category="security",
        anchor="x = 1",
    )


def test_inline_ids_round_trip() -> None:
    finding = _finding()

    fingerprint, identity = stamped_ids(render_inline_body(finding))

    assert fingerprint == finding_fingerprint(finding.path, finding.title)
    assert identity == finding_identity(finding)


def test_retired_ids_are_no_longer_read_as_active() -> None:
    """Resolve-on-fix moves both ids into the resolved family, so a finding
    that comes back after being fixed posts again instead of being suppressed."""
    finding = _finding()
    prose, ids = split_stamped_ids(render_inline_body(finding))

    retired = prose + retire_stamped_ids(ids)

    assert finding_keys(retired) == set()
    assert FINDING_MARKER.search(retired) is None
    assert IDENTITY_MARKER.search(retired) is None
    fingerprint = finding_fingerprint(finding.path, finding.title)
    assert f"<!-- lgtmaybe-resolved-fingerprint:{fingerprint} -->" in retired
    assert f"<!-- lgtmaybe-resolved-identity:{finding_identity(finding)} -->" in retired
    assert retired.startswith(prose)


_GATEWAY_PACKAGES = ("github", "gitlab", "gitea", "azure_devops")


def _docstring_nodes(tree: ast.Module) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, owners) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


# A marker family's prefix, e.g. ``lgtmaybe-reviewed:``.
_FAMILY = re.compile(r"lgtmaybe-[a-z-]+:")


def _gateway_sources() -> list[Path]:
    root = Path(lgtmaybe.__file__).parent
    return sorted(p for pkg in _GATEWAY_PACKAGES for p in (root / pkg).rglob("*.py"))


def test_gateway_sources_are_found() -> None:
    assert {p.parent.name for p in _gateway_sources()} >= set(_GATEWAY_PACKAGES)


@pytest.mark.parametrize("path", _gateway_sources(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_gateway_spells_out_a_hidden_marker(path: Path) -> None:
    """Gateways stamp markers through the ``core.comment`` writers only.

    Docstrings may describe a marker; code may not build one, f-string parts
    included, because a hand-built marker is the drift this file guards. A
    constant counts when it opens a comment naming lgtmaybe, or names a marker
    family (``lgtmaybe-<family>:``) on its own, as an f-string part would.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    offenders = [
        f"line {node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and (("<!--" in node.value and "lgtmaybe" in node.value) or _FAMILY.search(node.value))
    ]

    assert not offenders, f"{path.name} builds a hidden marker by hand: {offenders}"
