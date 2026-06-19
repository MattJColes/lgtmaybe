"""Structural checks on the eval fixtures — they load and parse as expected.

These are pure (no model): they guard that a fixture's diff is well-formed and
that the large multi-file fixture really exercises the multi-file path, so a
broken fixture fails fast in the pytest gate rather than only in the live
ollama e2e run.
"""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize("name", ["rlm-bigfile", "rlm-pipeline"])
def test_rlm_fixture_is_one_multi_hunk_file(name: str) -> None:
    """Each RLM benchmark fixture must be a single file with several hunks — that's
    the shape that exercises the recursive walk (an over-budget file split into
    per-hunk calls). Guards against an edit that flattens one to a single hunk and
    silently makes the benchmark a no-op."""
    from lgtmaybe.engine.compress import split_patch_into_hunks

    diff, manifest = _fixture(name)
    parts = split_by_file(diff, [manifest.changed_file])
    assert len(parts) == 1, f"{name} must be a single file"
    _path, patch = parts[0]
    assert len(split_patch_into_hunks(patch)) >= 3, f"{name} needs several hunks"


def test_cross_file_fp_fixture_has_expected_and_forbidden() -> None:
    """The cross-file fixture loads with a genuine in-diff catch plus forbidden traps —
    the diff alone (no sibling file_contents) so the guard is genuinely unseen, which is
    the real-world shape that produced the invalid findings."""
    diff, manifest = _fixture("cross-file-fp")
    assert diff.strip()
    assert manifest.expected, "needs a real in-diff finding so recall stays meaningful"
    assert manifest.forbidden, "needs forbidden (cross-file false-positive) traps"


def test_fixtures_cover_performance_and_complexity_lenses() -> None:
    """Both fixtures plant a performance and a complexity issue so the e2e exercises
    all seven code lenses, not just security + correctness. (The intent lens needs a
    stated intent the fixtures don't carry, so the engine skips it there.) Guards
    against a future edit silently dropping these lower-severity lenses from the
    live recall check."""
    for name in ("badcode", "vibe-multifile"):
        _diff, manifest = _fixture(name)
        keywords = " ".join(k.lower() for e in manifest.expected for k in e.keywords)
        assert "n+1" in keywords or "quadratic" in keywords, f"{name}: no performance finding"
        assert "complexity" in keywords and "cyclomatic" in keywords, (
            f"{name}: no complexity finding"
        )
