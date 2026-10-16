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
    marker,
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


def test_github_summary_marker_text_is_unchanged() -> None:
    """Existing PRs carry this exact text; a drift would re-post the summary."""
    from lgtmaybe.github.rest_gateway import _MARKER

    assert marker("lgtmaybe", None) == "<!-- lgtmaybe -->"
    assert _MARKER == "<!-- lgtmaybe -->"


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


_FAMILY_NAME = re.compile(r"lgtmaybe-[a-z]+(?:-[a-z]+)*")


def _marker_families() -> set[str]:
    """Every hidden-marker family name, read from where the markers are defined.

    ``core.comment`` holds the comment markers' readers and writers, and every
    ``lgtmaybe-`` name in it is a marker family. The engine and CLI gates are
    taken from their marker constants rather than their module text, which also
    names worker threads ``lgtmaybe-…``.
    """
    from lgtmaybe.cli import SKIPPED_MARKER
    from lgtmaybe.core.models import RiskAssessment
    from lgtmaybe.engine.engine import INCOMPLETE_MARKER, LENSES_MARKER_PREFIX
    from lgtmaybe.engine.risk import risk_marker

    text = (Path(lgtmaybe.__file__).parent / "core" / "comment.py").read_text(encoding="utf-8")
    gates = [
        INCOMPLETE_MARKER,
        LENSES_MARKER_PREFIX,
        SKIPPED_MARKER,
        risk_marker(RiskAssessment(level="high")),
    ]
    return set(_FAMILY_NAME.findall("\n".join([text, *gates])))


def test_marker_families_are_derived() -> None:
    expected = {
        f"lgtmaybe-{family}"
        for family in (
            "reviewed",
            "diagrammed",
            "diff",
            "diagram",
            "describe",
            "finding",
            "identity",
            "resolved-fingerprint",
            "resolved-identity",
            "risk",
            "lenses",
            "incomplete",
            "skipped",
        )
    }

    assert _marker_families() == expected


# A marker family named anywhere in a constant, with or without the ``:`` its
# value follows, so a prefix completed at run time is caught too. Exact names
# only (no longer word after one), so a non-marker name such as the base
# checkout's ``lgtmaybe-base-`` temp-dir prefix is not.
_FAMILY = re.compile(
    "(?:"
    + "|".join(sorted(map(re.escape, _marker_families()), key=len, reverse=True))
    + r")(?![a-z-])"
)


def _hand_built_markers(source: str) -> list[str]:
    """String constants in *source* that build a hidden marker by hand.

    Docstrings may describe a marker; code may not build one, f-string parts
    included, because a hand-built marker is the drift this file guards. A
    constant counts when it opens a comment naming lgtmaybe, or names a marker
    family (``lgtmaybe-<family>``) anywhere in it.
    """
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    return [
        f"line {node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and (("<!--" in node.value and "lgtmaybe" in node.value) or _FAMILY.search(node.value))
    ]


@pytest.mark.parametrize(
    "source",
    [
        'body += f"\\n<!-- lgtmaybe-reviewed:{sha} -->"\n',
        'prefix = "lgtmaybe-reviewed"\nstamp = prefix + ":" + sha\n',
        'old = "lgtmaybe-resolved-identity:"\n',
        'family = "lgtmaybe-describe"\n',
        'summary = "<!-- lgtmaybe -->"\n',
    ],
)
def test_a_hand_built_marker_is_flagged(source: str) -> None:
    assert _hand_built_markers(source)


@pytest.mark.parametrize(
    "source",
    [
        'def stamp():\n    """Appends ``<!-- lgtmaybe-reviewed:<sha> -->`` via core."""\n',
        '"""Module docstring naming lgtmaybe-diff markers."""\nx = 1\n',
        'body += f"\\n{reviewed_marker(sha)}"\n',
        'dest = mkdtemp(prefix="lgtmaybe-base-")\n',
        'family = "lgtmaybe-diagrammer"\n',
    ],
)
def test_clean_code_is_not_flagged(source: str) -> None:
    assert _hand_built_markers(source) == []


def _gateway_sources() -> list[Path]:
    root = Path(lgtmaybe.__file__).parent
    return sorted(p for pkg in _GATEWAY_PACKAGES for p in (root / pkg).rglob("*.py"))


def test_gateway_sources_are_found() -> None:
    assert {p.parent.name for p in _gateway_sources()} >= set(_GATEWAY_PACKAGES)


@pytest.mark.parametrize("path", _gateway_sources(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_gateway_spells_out_a_hidden_marker(path: Path) -> None:
    """Gateways stamp markers through the ``core.comment`` writers only."""
    offenders = _hand_built_markers(path.read_text(encoding="utf-8"))

    assert not offenders, f"{path.name} builds a hidden marker by hand: {offenders}"
