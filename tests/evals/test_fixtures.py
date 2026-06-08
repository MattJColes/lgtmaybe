"""Structural checks on the eval fixtures — they load and parse as expected.

These are pure (no model): they guard that a fixture's diff is well-formed and
that the large multi-file fixture really exercises the multi-file path, so a
broken fixture fails fast in the pytest gate rather than only in the live
ollama e2e run.
"""

from __future__ import annotations

from evals import run as run_mod
from lgtmaybe.core.diffparse import split_by_file
from lgtmaybe.github import is_reviewable

_VIBE_FILES = {
    "src/api/handlers.py",
    "src/db/queries.py",
    "src/utils/shell.py",
    "src/auth/session.py",
    "config/settings.py",
    "src/api/pagination.py",
}


def _fixture(name: str):
    for diff, manifest in run_mod._load_fixtures():
        if manifest.name == name:
            return diff, manifest
    raise AssertionError(f"fixture {name!r} not found")


def test_all_fixtures_load() -> None:
    """Every fixture dir parses into a (diff, manifest) pair with expected findings."""
    fixtures = run_mod._load_fixtures()
    assert fixtures, "no fixtures discovered"
    for diff, manifest in fixtures:
        assert diff.strip()
        assert manifest.expected, f"{manifest.name} has no expected findings"


def test_vibe_multifile_spans_all_reviewable_files() -> None:
    """The large fixture splits into all six files and none is filtered as generated."""
    diff, manifest = _fixture("vibe-multifile")

    paths = {path for path, _ in split_by_file(diff, [manifest.changed_file])}
    assert paths == _VIBE_FILES

    # All of them must survive the reviewable filter (no lockfiles/vendored noise).
    assert all(is_reviewable(p) for p in paths)


def test_vibe_multifile_has_high_signal_and_subtle_findings() -> None:
    """The manifest mixes easy security catches with subtler correctness bugs."""
    _diff, manifest = _fixture("vibe-multifile")
    labels = " ".join(e.label.lower() for e in manifest.expected)
    # A 0.6B CI model should be able to clear the 0.2 recall bar on these.
    assert "sql injection" in labels
    assert "shell=true" in labels
    assert "eval()" in labels
    # ...and the subtler bugs that prove depth.
    assert "off-by-one" in labels
