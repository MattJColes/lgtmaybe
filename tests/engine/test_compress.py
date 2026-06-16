"""Tests for compress.py — token-aware patch fitting."""

from __future__ import annotations

from lgtmaybe.engine.compress import (
    _token_encoder,
    batch_files,
    count_tokens,
    expand_hunks,
    split_patch_into_hunks,
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

    expanded = expand_hunks(patch, _CONTENT, 2)

    # Two leading lines (c, d) and two trailing lines (g, h) are added as context.
    assert "\n c\n d\n" in expanded
    assert "\n g\n h\n" in expanded
    # Header line/length counts are widened by the added context on both sides.
    assert "@@ -3,6 +3,6 @@" in expanded


def test_expand_hunks_noop_when_n_zero() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -5,2 +5,2 @@\n e\n+E2\n"
    assert expand_hunks(patch, _CONTENT, 0) == patch


def test_expand_hunks_noop_when_no_content() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -5,2 +5,2 @@\n e\n+E2\n"
    assert expand_hunks(patch, None, 5) == patch


def test_expand_hunks_clamps_at_file_edges() -> None:
    # Hunk at the very top of the file: no leading context possible, and a huge
    # n must not read past either end.
    patch = "diff --git a/f.py b/f.py\n@@ -1,1 +1,1 @@\n a\n"
    expanded = expand_hunks(patch, _CONTENT, 100)

    # No phantom lines before line 1.
    assert "@@ -1," in expanded
    # Trailing context is clamped to the last real line (j) — no over-read.
    assert expanded.rstrip().endswith(" j")
