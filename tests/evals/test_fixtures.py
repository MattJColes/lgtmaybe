"""Structural checks on the eval fixtures — they load and parse as expected.

These are pure (no model): they guard that a fixture's diff is well-formed and
that the large multi-file fixture really exercises the multi-file path, so a
broken fixture fails fast in the pytest gate rather than only in the live
ollama e2e run.
"""

from __future__ import annotations

import pytest

from evals import run as run_mod
from lgtmaybe.core.diffparse import changed_line_index, split_by_file
from lgtmaybe.engine.compress import split_patch_into_hunks
from lgtmaybe.github import is_reviewable

# The four live false-positive fixtures Track C adds: each plants a genuine catch
# plus forbidden traps drawn from real over-eager reviewer claims.
_FP_FIXTURES = ["lazy-imports", "split-hunks", "cloud-semantics", "test-harness"]

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


def _changed_lines(diff: str, path: str, side: str = "RIGHT") -> set[int]:
    """The set of new-file (RIGHT) line numbers that the diff actually changes."""
    index = changed_line_index(diff)
    return {line for line, _text in index.get((path, side), [])}


@pytest.mark.parametrize("name", _FP_FIXTURES)
def test_fp_fixture_loads_with_expected_and_forbidden(name: str) -> None:
    """Each live FP fixture loads with a genuine catch AND forbidden traps, every
    entry has keywords, and every expected/forbidden line is a real changed line —
    mirrors the cross-file-fp coverage so a malformed fixture fails in the gate."""
    diff, manifest = _fixture(name)
    assert diff.strip()
    assert manifest.expected, f"{name}: needs a real in-diff catch so recall stays meaningful"
    assert manifest.forbidden, f"{name}: needs forbidden (false-positive) traps"
    assert all(e.keywords for e in manifest.expected), f"{name}: an expected entry has no keywords"
    assert all(f.keywords for f in manifest.forbidden), f"{name}: a forbidden entry has no keywords"

    changed = _changed_lines(diff, manifest.changed_file)
    assert changed, f"{name}: diff has no changed RIGHT lines"
    for entry in manifest.expected + manifest.forbidden:
        assert entry.line in changed, (
            f"{name}: line {entry.line} ({entry.label!r}) is not a changed line; "
            f"changed lines are {sorted(changed)}"
        )


def test_split_hunks_fixture_is_multi_hunk() -> None:
    """split-hunks must be a single file split into >=2 hunks that both touch the
    same def (signature in one hunk, body edit in another) — the exact shape that
    tempts a model into a bogus "duplicate definition" finding."""
    diff, manifest = _fixture("split-hunks")
    parts = split_by_file(diff, [manifest.changed_file])
    assert len(parts) == 1, "split-hunks must be a single file"
    _path, patch = parts[0]
    hunks = split_patch_into_hunks(patch)
    assert len(hunks) >= 2, "split-hunks needs at least two hunks"
    touching = [h for h in hunks if "process_batch" in h]
    assert len(touching) >= 2, "both hunks must touch the same def (process_batch)"


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
