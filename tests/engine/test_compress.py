"""Tests for compress.py — token-aware patch fitting."""

from __future__ import annotations

from lgtmaybe.engine.compress import (
    _token_encoder,
    batch_files,
    count_tokens,
    expand_hunks,
    split_patch_into_hunks,
    trailing_context_lines,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_SMALL_DIFF = "@@ -1,3 +1,4 @@\n context\n+added line\n context\n"
_FILE_BLOCK = "diff --git a/{name} b/{name}\n{diff}"


def test_token_encoder_is_cached() -> None:
    """The tiktoken encoder is built once and reused across count_tokens calls
    (it is loaded once per file during batching — building it each time is slow)."""
    assert _token_encoder() is _token_encoder()


def test_count_tokens_is_stable_and_positive() -> None:
    assert count_tokens("hello world") == count_tokens("hello world")
    assert count_tokens("x") >= 1


def _make_diff(n_files: int, lines_per_file: int = 5) -> list[tuple[str, str]]:
    """Return list of (path, patch) pairs."""
    result = []
    for i in range(n_files):
        lines = "\n".join(f"+line {j}" for j in range(lines_per_file))
        patch = f"@@ -0,0 +1,{lines_per_file} @@\n{lines}\n"
        result.append((f"file_{i}.py", patch))
    return result


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


def test_count_tokens_returns_int() -> None:
    assert isinstance(count_tokens("hello world"), int)


def test_count_tokens_scales_with_length() -> None:
    short = count_tokens("x")
    long = count_tokens("x " * 1000)
    assert long > short


# ---------------------------------------------------------------------------
# batch_files: single-call path
# ---------------------------------------------------------------------------


def test_small_pr_fits_one_batch() -> None:
    files = _make_diff(3)
    batches = batch_files(files, max_tokens=10_000)
    assert len(batches) == 1


def test_large_pr_stays_under_token_budget_per_batch() -> None:
    # 50 files × 200 lines each — well over a 2 000-token budget per batch
    files = _make_diff(50, lines_per_file=200)
    budget = 2_000
    batches = batch_files(files, max_tokens=budget)
    assert len(batches) > 1
    for batch in batches:
        combined = "\n".join(patch for _, patch in batch)
        assert count_tokens(combined) <= budget


def test_oversize_pr_is_bounded() -> None:
    files = _make_diff(100, lines_per_file=200)
    budget = 3_000
    batches = batch_files(files, max_tokens=budget)
    # number of batches must be bounded (≤ number of files in worst case)
    assert len(batches) <= len(files)


# ---------------------------------------------------------------------------
# split_patch_into_hunks: the RLM-style per-hunk decomposition
# ---------------------------------------------------------------------------


def _multi_hunk_file(path: str, n_hunks: int, lines_per_hunk: int) -> str:
    """A single-file patch with ``n_hunks`` hunks, each adding ``lines_per_hunk`` lines."""
    header = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    body = ""
    for h in range(n_hunks):
        start = h * 100 + 1
        body += f"@@ -{start},1 +{start},{lines_per_hunk} @@\n"
        body += "".join(f"+line {h}-{j}\n" for j in range(lines_per_hunk))
    return header + body


def test_split_patch_into_hunks_yields_one_unit_per_hunk() -> None:
    patch = _multi_hunk_file("big.py", n_hunks=3, lines_per_hunk=2)
    units = split_patch_into_hunks(patch)
    assert len(units) == 3
    # Each unit is a standalone mini-diff: it carries the file header and exactly
    # one @@ hunk, so it can be reviewed on its own.
    for unit in units:
        assert unit.startswith("diff --git a/big.py b/big.py\n")
        assert "+++ b/big.py\n" in unit
        assert unit.count("@@ -") == 1


def test_split_patch_into_hunks_single_hunk_is_unchanged() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -1,1 +1,2 @@\n a\n+b\n"
    assert split_patch_into_hunks(patch) == [patch]


def test_split_patch_into_hunks_no_hunk_returns_whole() -> None:
    # A pure rename/mode change with no @@ hunk is returned intact.
    patch = "diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py\n"
    assert split_patch_into_hunks(patch) == [patch]


# ---------------------------------------------------------------------------
# batch_files recursive=True: walk an over-budget file hunk-by-hunk (RLM)
# ---------------------------------------------------------------------------


def test_recursive_splits_oversize_file_into_hunk_batches() -> None:
    patch = _multi_hunk_file("big.py", n_hunks=6, lines_per_hunk=100)
    files = [("big.py", patch)]
    budget = count_tokens(patch) // 3  # the whole file is ~3× the budget

    # Default (non-recursive): the oversize file is sent WHOLE in its own batch —
    # over budget, so the model's context truncates the tail.
    whole = batch_files(files, max_tokens=budget)
    assert len(whole) == 1
    assert count_tokens(whole[0][0][1]) > budget

    # Recursive: split into per-hunk units so every batch fits the budget and
    # nothing is dropped. Each unit still carries the file's path.
    walked = batch_files(files, max_tokens=budget, recursive=True)
    assert len(walked) > 1
    for batch in walked:
        combined = "\n".join(p for _, p in batch)
        assert count_tokens(combined) <= budget
    assert all(path == "big.py" for batch in walked for path, _ in batch)


def test_recursive_leaves_within_budget_file_whole() -> None:
    # A file that already fits the budget is not split, even with recursive on —
    # the whole-file context is preserved when it costs nothing to keep.
    files = _make_diff(1, lines_per_file=3)
    batches = batch_files(files, max_tokens=10_000, recursive=True)
    assert len(batches) == 1
    assert len(batches[0]) == 1


# ---------------------------------------------------------------------------
# dynamic context: small PR gets more context lines than a big PR
# ---------------------------------------------------------------------------


def test_dynamic_context_more_for_small_pr() -> None:
    from lgtmaybe.engine.compress import context_lines_for_budget

    small_pr_tokens_used = 500
    large_pr_tokens_used = 90_000
    budget = 100_000

    ctx_small = context_lines_for_budget(budget - small_pr_tokens_used)
    ctx_large = context_lines_for_budget(budget - large_pr_tokens_used)

    assert ctx_small > ctx_large
    assert ctx_small >= 0
    assert ctx_large >= 0


# ---------------------------------------------------------------------------
# expand_hunks: pad hunks with surrounding lines from head file content
# ---------------------------------------------------------------------------

_CONTENT = "\n".join("abcdefghij")  # lines 1..10: a, b, c, ... j


def test_expand_hunks_adds_surrounding_lines() -> None:
    # Hunk covers new-file lines 5..6 (e, E2); ask for 2 lines either side.
    patch = "diff --git a/f.py b/f.py\n@@ -5,2 +5,2 @@\n e\n+E2\n"

    expanded = expand_hunks(patch, _CONTENT, 2, after=2)

    # Two leading lines (c, d) and two trailing lines (g, h) are added as context.
    assert "\n c\n d\n" in expanded
    assert "\n g\n h\n" in expanded
    # Header line/length counts are widened by the added context on both sides.
    assert "@@ -3,6 +3,6 @@" in expanded


def test_expand_hunks_noop_when_n_zero() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -5,2 +5,2 @@\n e\n+E2\n"
    assert expand_hunks(patch, _CONTENT, 0, after=0) == patch


def test_expand_hunks_noop_when_no_content() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -5,2 +5,2 @@\n e\n+E2\n"
    assert expand_hunks(patch, None, 5, after=1) == patch


def test_expand_hunks_clamps_at_file_edges() -> None:
    # Hunk at the very top of the file: no leading context possible, and a huge
    # n must not read past either end.
    patch = "diff --git a/f.py b/f.py\n@@ -1,1 +1,1 @@\n a\n"
    expanded = expand_hunks(patch, _CONTENT, 100, after=100)

    # No phantom lines before line 1.
    assert "@@ -1," in expanded
    # Trailing context is clamped to the last real line (j) — no over-read.
    assert expanded.rstrip().endswith(" j")


def test_expand_hunks_asymmetric_pads_fewer_after() -> None:
    # The code BEFORE a change (signature, setup) explains it better than the
    # code after, so an explicit `after` pads the two sides differently.
    patch = "diff --git a/f.py b/f.py\n@@ -5,2 +5,2 @@\n e\n+E2\n"

    expanded = expand_hunks(patch, _CONTENT, 3, after=1)

    # Three leading lines (b, c, d) and exactly one trailing line (g).
    assert "\n b\n c\n d\n" in expanded
    assert "\n g\n" in expanded
    assert " h\n" not in expanded
    # Header widened by 3 leading + 1 trailing = 4.
    assert "@@ -2,6 +2,6 @@" in expanded


def test_trailing_context_lines_ratio() -> None:
    # PR-Agent-style asymmetry: roughly a quarter of the leading budget,
    # floored at one line so the model still sees what follows the change.
    assert trailing_context_lines(20) == 5
    assert trailing_context_lines(4) == 1
    assert trailing_context_lines(1) == 1
    assert trailing_context_lines(0) == 0


# ---------------------------------------------------------------------------
# expand_hunks: boundary-aware leading pad (P4 remainder)
# ---------------------------------------------------------------------------

# 30 lines: a def on line 3, hunk will sit far below it.
_FN_CONTENT = "\n".join(
    ["import os", "", "def handler(req):"]
    + [f"    step_{i}()" for i in range(1, 26)]
    + ["    return done"]
)


def test_boundary_extends_the_leading_pad_to_the_enclosing_def() -> None:
    # Hunk at new-file line 20 with a 2-line fixed pad; the enclosing def is on
    # line 3 — well above the fixed window, within reach.
    patch = "diff --git a/f.py b/f.py\n@@ -20,1 +20,1 @@\n step_17()\n"

    expanded = expand_hunks(patch, _FN_CONTENT, 2, after=1, boundaries=[3])

    assert "\ndef handler(req):\n" in expanded or "\n def handler(req):\n" in expanded
    # Header start moved up to the boundary line.
    assert "@@ -3," in expanded


def test_boundary_inside_the_fixed_window_changes_nothing() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -4,1 +4,1 @@\n step_1()\n"

    with_boundary = expand_hunks(patch, _FN_CONTENT, 5, after=1, boundaries=[3])
    without = expand_hunks(patch, _FN_CONTENT, 5, after=1)

    # The def on line 3 is already inside the 5-line fixed pad — the boundary
    # must never SHRINK the window.
    assert with_boundary == without


def test_boundary_beyond_reach_is_ignored() -> None:
    lines = ["def far_away():"] + [f"    l{i}" for i in range(1, 400)]
    content = "\n".join(lines)
    patch = "diff --git a/f.py b/f.py\n@@ -300,1 +300,1 @@\n l299\n"

    expanded = expand_hunks(patch, content, 2, after=1, boundaries=[1])

    assert "def far_away" not in expanded  # 299 lines up: past the reach cap


def test_no_boundaries_keeps_the_fixed_pad() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -20,1 +20,1 @@\n step_17()\n"
    assert expand_hunks(patch, _FN_CONTENT, 2, after=1, boundaries=[]) == expand_hunks(
        patch, _FN_CONTENT, 2, after=1
    )


def test_count_tokens_memoizes_repeated_text() -> None:
    """The same text is token-counted many times per review (batching, the
    reflection reserve on every deferral hop) — repeats must hit a cache, not
    re-encode the whole string."""
    count_tokens.cache_clear()
    text = "some diff text " * 200
    first = count_tokens(text)
    assert count_tokens(text) == first
    info = count_tokens.cache_info()
    assert info.hits >= 1
    assert info.misses == 1
