"""Tests for compress.py — token-aware patch fitting."""

from __future__ import annotations

from lgtmaybe.core.diffparse import HunkHeader, parse_hunk_header
from lgtmaybe.engine.compress import (
    _enclosing_boundary,
    _token_encoder,
    batch_files,
    count_tokens,
    expand_hunks,
    split_hunk_by_budget,
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
    patch = "diff --git a/f.py b/f.py\n@@ -5,1 +5,2 @@\n e\n+E2\n"

    expanded = expand_hunks(patch, _CONTENT, 2, after=2)

    # Two leading lines (c, d) and two trailing lines (g, h) are added as context.
    assert "\n c\n d\n" in expanded
    assert "\n g\n h\n" in expanded
    # Header line/length counts are widened by the added context on both sides:
    # one old line + 4 pads, two new lines + 4 pads, both starting two lines up.
    assert "@@ -3,5 +3,6 @@" in expanded


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
    patch = "diff --git a/f.py b/f.py\n@@ -5,1 +5,2 @@\n e\n+E2\n"

    expanded = expand_hunks(patch, _CONTENT, 3, after=1)

    # Three leading lines (b, c, d) and exactly one trailing line (g).
    assert "\n b\n c\n d\n" in expanded
    assert "\n g\n" in expanded
    assert " h\n" not in expanded
    # Header widened by 3 leading + 1 trailing = 4 on each side.
    assert "@@ -2,5 +2,6 @@" in expanded


def test_trailing_context_lines_ratio() -> None:
    # PR-Agent-style asymmetry: roughly a quarter of the leading budget,
    # floored at one line so the model still sees what follows the change.
    assert trailing_context_lines(20) == 5
    assert trailing_context_lines(4) == 1
    assert trailing_context_lines(1) == 1
    assert trailing_context_lines(0) == 0


def test_expand_hunks_header_old_start_never_below_one() -> None:
    """When earlier hunks net-add lines, a later hunk's old_start sits far below
    its new_start; the leading pad must be clamped so the rewritten old start
    stays >= 1 — a negative old start yields a header parse_hunk_header rejects,
    which mis-numbers every line downstream in _snap_findings."""
    from lgtmaybe.core.diffparse import parse_hunk_header

    added = "\n".join(f"+line{i}" for i in range(1, 101))
    patch = (
        "diff --git a/f.py b/f.py\n"
        "@@ -1,1 +1,101 @@\n old_first\n" + added + "\n"
        "@@ -5,2 +105,2 @@\n ctx\n+added\n"
    )
    content = "\n".join(f"c{i}" for i in range(1, 200))  # 199 lines

    expanded = expand_hunks(patch, content, 20, after=5)

    # The second hunk sits within the first one's trailing pad, so the two are
    # emitted as one merged hunk — the clamp still has to hold for its header.
    headers = [ln for ln in expanded.splitlines() if ln.startswith("@@")]
    assert headers
    for line in headers:
        header = parse_hunk_header(line)
        assert header is not None, f"unparseable rewritten header: {line!r}"
        assert header.old_start >= 1
        assert header.new_start >= 1


def test_expand_hunks_never_raises_when_content_shorter_than_hunk() -> None:
    """Redaction (or stale head text) can leave the file shorter than the hunk
    positions; reads must be clamped to the file's bounds — degrade to less
    padding, never an IndexError that fails the whole review."""
    content = "\n".join(f"l{i}" for i in range(1, 101))  # 100 lines
    patch = "diff --git a/f.py b/f.py\n@@ -125,2 +125,2 @@\n ctx\n+added\n"

    expanded = expand_hunks(patch, content, 20, after=5)  # must not raise

    assert "@@" in expanded
    assert "+added" in expanded


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

    expanded = expand_hunks(patch, _FN_CONTENT, 2, after=1, boundaries=[(3, 29)])

    assert "\ndef handler(req):\n" in expanded or "\n def handler(req):\n" in expanded
    # Header start moved up to the boundary line.
    assert "@@ -3," in expanded


def test_boundary_inside_the_fixed_window_changes_nothing() -> None:
    patch = "diff --git a/f.py b/f.py\n@@ -4,1 +4,1 @@\n step_1()\n"

    with_boundary = expand_hunks(patch, _FN_CONTENT, 5, after=1, boundaries=[(3, 29)])
    without = expand_hunks(patch, _FN_CONTENT, 5, after=1)

    # The def on line 3 is already inside the 5-line fixed pad — the boundary
    # must never SHRINK the window.
    assert with_boundary == without


def test_boundary_beyond_reach_is_ignored() -> None:
    lines = ["def far_away():"] + [f"    l{i}" for i in range(1, 400)]
    content = "\n".join(lines)
    patch = "diff --git a/f.py b/f.py\n@@ -300,1 +300,1 @@\n l299\n"

    expanded = expand_hunks(patch, content, 2, after=1, boundaries=[(1, 400)])

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


# ---------------------------------------------------------------------------
# split_hunk_by_budget: the tail of the RLM walk — one hunk bigger than the budget
# ---------------------------------------------------------------------------


def _new_file_patch(path: str, lines: int) -> str:
    """A brand-new file: one file header, ONE hunk covering every line."""
    header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    body = f"@@ -0,0 +1,{lines} @@\n" + "".join(f"+line_{i} = {i}\n" for i in range(lines))
    return header + body


def _added_line_numbers(unit: str) -> list[int]:
    """(line number, text) of every added line in *unit*, per its own @@ header."""
    out: list[int] = []
    new_line = 0
    for raw in unit.splitlines():
        if raw.startswith("@@"):
            new_line = int(raw.split("+")[1].split(",")[0].split(" ")[0])
            continue
        if raw.startswith("---") or raw.startswith("+++") or raw.startswith("diff --git"):
            continue
        if raw.startswith("-"):
            continue
        if raw.startswith("+"):
            out.append(new_line)
        new_line += 1
    return out


def test_split_hunk_by_budget_fits_every_piece() -> None:
    patch = _new_file_patch("new.py", 400)
    budget = count_tokens(patch) // 4

    pieces = split_hunk_by_budget(patch, budget)

    assert len(pieces) > 1
    for piece in pieces:
        assert count_tokens(piece) <= budget


def test_split_hunk_by_budget_preserves_line_numbers() -> None:
    """A finding's line must still bind to the real file, so each piece's @@
    header has to say where in the file its slice starts."""
    patch = _new_file_patch("new.py", 200)
    pieces = split_hunk_by_budget(patch, count_tokens(patch) // 5)

    seen = [n for piece in pieces for n in _added_line_numbers(piece)]
    assert seen == list(range(1, 201))


def test_split_hunk_by_budget_keeps_the_file_header() -> None:
    patch = _new_file_patch("pkg/new.py", 200)
    for piece in split_hunk_by_budget(patch, count_tokens(patch) // 4):
        assert "+++ b/pkg/new.py" in piece
        assert piece.count("@@ -") == 1


def test_split_hunk_by_budget_loses_no_content() -> None:
    patch = _new_file_patch("new.py", 150)
    pieces = split_hunk_by_budget(patch, count_tokens(patch) // 3)

    body = [ln for p in pieces for ln in p.splitlines() if ln.startswith("+line_")]
    original = [ln for ln in patch.splitlines() if ln.startswith("+line_")]
    assert body == original


def test_split_hunk_by_budget_counts_both_sides() -> None:
    """A mixed hunk moves the old-side and new-side counters independently."""
    header = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
    body = "@@ -10,60 +20,60 @@\n" + "".join(f" ctx_{i}\n-old_{i}\n+new_{i}\n" for i in range(30))
    pieces = split_hunk_by_budget(header + body, 60)

    assert len(pieces) > 1
    spans = []
    for piece in pieces:
        text = piece.split("@@ ")[1].split(" @@")[0]
        old, new = text.split(" ")
        spans.append(tuple(int(v) for v in old[1:].split(",") + new[1:].split(",")))
    # Each side is counted independently and every piece resumes exactly where
    # the previous one stopped — no gap, no overlap, on either side.
    assert spans[0][0] == 10 and spans[0][2] == 20
    for (o_start, o_count, n_start, n_count), nxt in zip(spans, spans[1:], strict=False):
        assert nxt[0] == o_start + o_count
        assert nxt[2] == n_start + n_count
    # A hunk of ctx/-/+ triples moves the old side and the new side by the same
    # amount overall, but neither counter is derived from the other.
    assert sum(s[1] for s in spans) == 60
    assert sum(s[3] for s in spans) == 60


def test_split_hunk_by_budget_returns_whole_when_it_cannot_split() -> None:
    """One line bigger than the whole budget can't be divided any further —
    return it rather than loop forever or drop it."""
    header = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
    patch = header + "@@ -0,0 +1,1 @@\n+" + "x " * 5000 + "\n"

    pieces = split_hunk_by_budget(patch, 10)

    assert len(pieces) == 1
    assert pieces[0] == patch


def test_split_hunk_by_budget_no_hunk_returns_whole() -> None:
    patch = "diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py\n"
    assert split_hunk_by_budget(patch, 10) == [patch]


def test_recursive_splits_a_single_oversize_hunk() -> None:
    """A brand-new file is ONE hunk, so hunk-splitting alone left it whole and
    the token budget was silently ignored."""
    patch = _new_file_patch("new.py", 600)
    budget = count_tokens(patch) // 5

    walked = batch_files([("new.py", patch)], max_tokens=budget, recursive=True)

    assert len(walked) > 1
    for batch in walked:
        assert count_tokens("\n".join(p for _, p in batch)) <= budget


# overlapping expansion
# ---------------------------------------------------------------------------

_LONG_CONTENT = "\n".join(f"line{i}" for i in range(1, 61))

# Two hunks ten lines apart. A 15-line leading pad on the second reaches back
# past the first hunk's own body, so unmerged expansion emits the span twice.
_NEARBY_HUNKS = (
    "diff --git a/f.py b/f.py\n"
    "@@ -20,2 +20,2 @@\n line20\n-line21\n+CHANGED21\n"
    "@@ -30,2 +30,2 @@\n line30\n-line31\n+CHANGED31\n"
)


def _hunk_ranges(expanded: str) -> list[tuple[int, int]]:
    """(start, end) new-file line range of every hunk in *expanded*."""
    ranges = []
    for line in expanded.splitlines():
        header = parse_hunk_header(line)
        if header is not None:
            ranges.append((header.new_start, header.new_start + header.new_len - 1))
    return ranges


def test_expanded_hunks_never_overlap() -> None:
    """Expansion must not emit hunks whose line ranges overlap.

    Each hunk is padded independently, so two nearby hunks can each reach into
    the other's span. That yields a non-monotonic patch — a later hunk header
    pointing *above* where the previous one ended — which breaks the line
    arithmetic the model is asked to do, on top of paying for the span twice.
    """
    expanded = expand_hunks(_NEARBY_HUNKS, _LONG_CONTENT, 15, after=3)

    ranges = _hunk_ranges(expanded)
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:], strict=False):
        assert next_start > prev_end, f"hunks overlap: {ranges}"


def test_expanded_hunks_do_not_repeat_context_lines() -> None:
    """No source line may appear twice in the expanded patch.

    Beyond the wasted tokens, a changed line repeated as unmerged context shows
    the model the same position with two different contents.
    """
    expanded = expand_hunks(_NEARBY_HUNKS, _LONG_CONTENT, 15, after=3)

    body = [ln[1:] for ln in expanded.splitlines() if ln[:1] in {" ", "+", "-"}]
    assert len(body) == len(set(body)), f"duplicated lines: {expanded}"


def test_merged_hunk_keeps_every_change() -> None:
    """Merging two overlapping hunks keeps both changes, in file order."""
    expanded = expand_hunks(_NEARBY_HUNKS, _LONG_CONTENT, 15, after=3)

    assert expanded.count("+CHANGED21") == 1
    assert expanded.count("+CHANGED31") == 1
    assert expanded.index("CHANGED21") < expanded.index("CHANGED31")
    assert expanded.count("-line21") == 1
    assert expanded.count("-line31") == 1


def _hunks_with_bodies(expanded: str) -> list[tuple[HunkHeader, list[str]]]:
    """Every hunk in *expanded* paired with the body lines that follow it."""
    hunks: list[tuple[HunkHeader, list[str]]] = []
    for line in expanded.splitlines():
        header = parse_hunk_header(line)
        if header is not None:
            hunks.append((header, []))
        elif hunks:
            hunks[-1][1].append(line)
    return hunks


def test_merged_hunk_header_counts_match_its_body() -> None:
    """A merged hunk's header lengths must describe the lines it really holds."""
    expanded = expand_hunks(_NEARBY_HUNKS, _LONG_CONTENT, 15, after=3)

    hunks = _hunks_with_bodies(expanded)
    assert hunks
    for header, body in hunks:
        assert header.old_len == sum(1 for ln in body if ln[:1] in {" ", "-"})
        assert header.new_len == sum(1 for ln in body if ln[:1] in {" ", "+"})


def test_distant_hunks_stay_separate() -> None:
    """Hunks whose padded windows do not meet keep their own headers."""
    patch = (
        "diff --git a/f.py b/f.py\n"
        "@@ -5,1 +5,1 @@\n-line5\n+CHANGED5\n"
        "@@ -50,1 +50,1 @@\n-line50\n+CHANGED50\n"
    )
    expanded = expand_hunks(patch, _LONG_CONTENT, 2, after=1)

    assert len(_hunk_ranges(expanded)) == 2


def test_module_level_hunk_is_not_padded_into_a_closed_definition() -> None:
    """A definition that has already ended does not enclose a later hunk.

    Matching on the nearest start alone pulled module-level code — constants,
    config tables, registries — back into the body of whatever function happened
    to sit above it: wrong context for the model, and paid for on every lens.
    """
    content = "\n".join(
        # 1: import os   2: (blank)   3: def helper(a)   4..23: step_1..step_20
        ["import os", ""]
        + ["def helper(a):"]
        + [f"    step_{i}()" for i in range(1, 21)]
        # 24: return a   25,26: (blank)   27: CONFIG = {   28: alpha  29: beta  30: }
        + ["    return a", "", "", "CONFIG = {", '    "alpha": 1,', '    "beta": 2,', "}"]
    )
    # helper() spans lines 3..24; the change is on line 29, inside CONFIG.
    patch = 'diff --git a/f.py b/f.py\n@@ -29,1 +29,1 @@\n     "beta": 2,\n'

    expanded = expand_hunks(patch, content, 2, after=1, boundaries=[(3, 24)])

    assert "def helper" not in expanded
    assert "step_20()" not in expanded
    # The pad is still doing its job — the two lines above the change and the
    # one below are there, so an expansion that broke entirely (or returned an
    # empty string) could not pass on the absences alone.
    assert "CONFIG = {" in expanded
    assert '"alpha": 1,' in expanded
    assert expanded.rstrip().endswith("}")


def test_innermost_enclosing_definition_wins() -> None:
    """A method inside a class resolves to the method, not the class."""
    spans = [(1, 40), (10, 20)]  # class 1..40, method 10..20

    assert _enclosing_boundary(spans, 15) == 10
    # Above the method but still inside the class body.
    assert _enclosing_boundary(spans, 5) == 1


def test_no_overlap_when_head_text_is_shorter_than_the_hunks() -> None:
    """The non-overlap invariant must hold on truncated/stale head text too.

    When two hunks' pads reach each other but the gap cannot be filled — the
    file is shorter than the hunk positions — they cannot be merged without
    dropping lines the merged header would claim. The later hunk's leading pad
    is trimmed to clear the previous one instead. Found by fuzzing.
    """
    content = "\n".join(f"c{i}" for i in range(1, 42))  # 41 lines
    patch = "diff --git a/f.py b/f.py\n@@ -45,1 +45,1 @@\n x45\n@@ -47,1 +47,1 @@\n x47\n"

    expanded = expand_hunks(patch, content, 16, after=4)

    ranges = _hunk_ranges(expanded)
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:], strict=False):
        assert next_start > prev_end, f"hunks overlap: {ranges}"
    # Both changes survive the trim.
    assert "x45" in expanded and "x47" in expanded


def test_boundary_reach_is_proportionate_to_the_fixed_pad() -> None:
    """The enclosing-definition pad may not dwarf the pad it widens.

    The reach exists so a hunk deep in a function still sees the signature. But
    it pads with every intervening line, not just the signature, so a reach many
    times `_MAX_CONTEXT_LINES` stops adding context and starts replacing the
    diff with an unrelated function body — the very thing the cap exists to
    prevent. Keep it a small multiple of the largest fixed pad.
    """
    from lgtmaybe.engine.compress import _MAX_BOUNDARY_REACH, _MAX_CONTEXT_LINES

    assert _MAX_BOUNDARY_REACH <= 2 * _MAX_CONTEXT_LINES


def test_a_definition_far_above_the_hunk_is_out_of_reach() -> None:
    """A hunk 60 lines into a long function keeps the fixed pad."""
    content = "\n".join(["def long_one():"] + [f"    l{i}" for i in range(1, 100)])

    expanded = expand_hunks(
        "diff --git a/f.py b/f.py\n@@ -61,1 +61,1 @@\n     l60\n",
        content,
        2,
        after=1,
        boundaries=[(1, 100)],
    )

    assert "def long_one" not in expanded
