"""Token-aware patch fitting.

Splits changed files into batches that each fit within a token budget.
Provides a dynamic context-line calculator for the remaining budget.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from operator import itemgetter
from typing import Any

from lgtmaybe.core.diffparse import HunkHeader, parse_hunk_header

_MAX_CONTEXT_LINES = 20

# Scale: remaining_tokens / _SCALE gives context lines, capped at _MAX_CONTEXT_LINES.
# At 100k budget with 500 tokens used → 99,500 / 5000 = 19 lines.
# At 100k budget with 90k tokens used → 10,000 / 5000 = 2 lines.
_SCALE = 5_000


@lru_cache(maxsize=1)
def _token_encoder() -> Any | None:
    """Return a cached tiktoken encoder, or None if tiktoken is unavailable.

    Building the encoder is slow, and ``count_tokens`` runs once per file during
    batching — so resolve it once and reuse it rather than re-importing and
    re-loading the encoding on every call.
    """
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


@lru_cache(maxsize=256)
def count_tokens(text: str) -> int:
    """Return the token count for *text* using tiktoken, with a len/4 fallback.

    Memoized: the same text is counted repeatedly across a review — each
    over-budget patch twice during recursive batching, the whole diff once per
    reflection deferral hop — and encoding is O(len) each time. Bounded so the
    cache can't retain an unbounded number of large diff strings.
    """
    enc = _token_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)


def split_patch_into_hunks(patch: str) -> list[str]:
    """Split one file's patch into standalone single-hunk mini-diffs.

    Each returned string carries the file header (the ``diff --git`` / ``---`` /
    ``+++`` lines) followed by exactly one ``@@`` hunk, so it is a valid diff that
    can be reviewed on its own — the unit an RLM-style walk recurses over. A patch
    with no ``@@`` hunk (a pure rename/mode change) is returned whole. The original
    hunk headers (and therefore line numbers) are preserved, so a finding's
    line/side still binds to the real diff when comments are posted.
    """
    lines = patch.splitlines(keepends=True)
    first_hunk = next((i for i, ln in enumerate(lines) if ln.startswith("@@")), None)
    if first_hunk is None:
        return [patch]

    header = lines[:first_hunk]
    units: list[str] = []
    current: list[str] = []
    for line in lines[first_hunk:]:
        if line.startswith("@@") and current:
            units.append("".join(header + current))
            current = []
        current.append(line)
    if current:
        units.append("".join(header + current))
    return units


def split_hunk_by_budget(unit: str, max_tokens: int) -> list[str]:
    """Split one over-budget single-hunk mini-diff into budget-sized slices.

    :func:`split_patch_into_hunks` can only cut at ``@@`` boundaries, so a file
    whose diff is ONE enormous hunk — every brand-new file is exactly that —
    came back undivided and was sent whole, silently ignoring the token budget
    the RLM walk exists to respect. This is the tail of that walk: it cuts
    *inside* a hunk and writes each slice a fresh ``@@`` header, so a finding's
    line still binds to the real file.

    Each slice carries the original file header and one synthesised hunk header.
    A hunk with no ``@@`` line, or one whose very first body line already blows
    the budget, is returned whole — there is nothing smaller to cut it into, and
    dropping it would be worse than sending it oversized.
    """
    lines = unit.splitlines(keepends=True)
    hunk_at = next((i for i, ln in enumerate(lines) if ln.startswith("@@")), None)
    if hunk_at is None:
        return [unit]
    parsed = parse_hunk_header(lines[hunk_at])
    if parsed is None:
        return [unit]

    header = "".join(lines[:hunk_at])
    tail = parsed.section
    body = lines[hunk_at + 1 :]
    if not body:
        return [unit]

    # Running position of each body line on both sides. A "\ No newline at end
    # of file" marker annotates the line before it and counts on neither side.
    old_at = [parsed.old_start]
    new_at = [parsed.new_start]
    for line in body:
        marker = line[:1]
        old_at.append(old_at[-1] + (0 if marker in ("+", "\\") else 1))
        new_at.append(new_at[-1] + (0 if marker in ("-", "\\") else 1))
    costs = [count_tokens(line) for line in body]

    def render(start: int, end: int) -> str:
        return (
            f"{header}@@ -{old_at[start]},{old_at[end] - old_at[start]} "
            f"+{new_at[start]},{new_at[end] - new_at[start]} @@{tail}\n" + "".join(body[start:end])
        )

    slices: list[str] = []
    start = 0
    while start < len(body):
        # Grow greedily on the per-line estimate, then verify: summing token
        # counts line by line misses the merges a tokenizer makes across a line
        # break, so the estimate runs under the truth by a good 10%. Shrink
        # proportionally until the rendered slice really fits — a couple of
        # measurements, not a scan.
        end = start + 1
        used = costs[start]
        while end < len(body) and used + costs[end] <= max_tokens:
            used += costs[end]
            end += 1
        while end > start + 1:
            actual = count_tokens(render(start, end))
            if actual <= max_tokens:
                break
            end = max(start + 1, start + (end - start) * max_tokens // actual)
        slices.append(render(start, end))
        start = end

    # One body line larger than the whole budget yields a single slice — no
    # progress over the input, so hand back the original rather than a rewrite.
    return slices if len(slices) > 1 else [unit]


def batch_files(
    files: list[tuple[str, str]],
    max_tokens: int,
    *,
    recursive: bool = False,
) -> list[list[tuple[str, str]]]:
    """Partition *files* into batches where each batch's combined patch fits under *max_tokens*.

    Args:
        files: List of (path, patch) pairs.
        max_tokens: Token budget per batch.
        recursive: When True, a single file that exceeds the budget is walked
            hunk-by-hunk (RLM-style) — split into per-hunk units that are then
            batched normally — instead of being sent whole (where the model's
            context would drop the tail). Files that already fit are untouched.

    Returns:
        A list of batches; each batch is a list of (path, patch) pairs.
    """
    # RLM walk: decompose any over-budget file into per-hunk units up front, so a
    # large file becomes several small calls that each fit instead of one
    # oversized call. Files within budget pass through whole (context preserved).
    if recursive:
        units: list[tuple[str, str]] = []
        for path, patch in files:
            if count_tokens(patch) < max_tokens:
                units.append((path, patch))
                continue
            # Cut at hunk boundaries first (the natural seams), then cut inside
            # any hunk that is still too big on its own — otherwise a one-hunk
            # file, which is what every new file looks like, escapes the budget.
            for hunk in split_patch_into_hunks(patch):
                if count_tokens(hunk) >= max_tokens:
                    units.extend((path, piece) for piece in split_hunk_by_budget(hunk, max_tokens))
                else:
                    units.append((path, hunk))
        files = units

    batches: list[list[tuple[str, str]]] = []
    current_batch: list[tuple[str, str]] = []
    current_tokens = 0

    for path, patch in files:
        file_tokens = count_tokens(patch)

        # If a single file exceeds the budget on its own, give it its own batch.
        if file_tokens >= max_tokens:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            batches.append([(path, patch)])
            continue

        if current_tokens + file_tokens > max_tokens and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append((path, patch))
        current_tokens += file_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def context_lines_for_budget(remaining_tokens: int) -> int:
    """Return how many extra context lines to expand hunks by, given the remaining token budget.

    A larger remaining budget yields more context; a smaller one yields less.
    The result is capped between 0 and _MAX_CONTEXT_LINES.
    """
    # Scale: every _SCALE remaining tokens buys one context line,
    # up to _MAX_CONTEXT_LINES.
    return max(0, min(remaining_tokens // _SCALE, _MAX_CONTEXT_LINES))


def _enclosing_boundary(boundaries: list[tuple[int, int]], new_start: int) -> int | None:
    """The start line of the innermost definition CONTAINING *new_start*.

    *boundaries* is a sorted list of inclusive ``(start, end)`` spans. A
    definition encloses *new_start* only when it has not already closed above
    it: matching on start alone made module-level code look enclosed by
    whatever function happened to sit above it, padding the hunk back into an
    unrelated body. Walking back from the nearest start yields the innermost
    enclosing definition (a method before its class).

    None when nothing encloses it, or when the enclosing definition begins more
    than :data:`_MAX_BOUNDARY_REACH` lines above (padding would drown the diff).
    """
    idx = bisect_right(boundaries, new_start, key=itemgetter(0)) - 1
    while idx >= 0:
        start, end = boundaries[idx]
        if new_start - start > _MAX_BOUNDARY_REACH:
            # Sorted by start, so everything left of here is further away still.
            return None
        if end >= new_start:
            return start
        idx -= 1
    return None


def trailing_context_lines(before: int) -> int:
    """The trailing pad for a leading pad of *before* lines (asymmetric context).

    The code BEFORE a change — the enclosing signature, setup, and definitions —
    explains it far better than the code after, so the trailing side gets roughly
    a quarter of the leading budget (PR-Agent weights its dynamic context the
    same way), floored at one line so the model still sees what follows. Zero
    stays zero: no leading pad means expansion is off entirely.
    """
    if before <= 0:
        return 0
    return max(1, before // 4)


# How far above a hunk the enclosing-definition pad may reach (lines). Beyond
# this the "enclosing function" is so far away that padding to it would drown
# the diff; the fixed-line pad applies instead.
#
# Sized against `_MAX_CONTEXT_LINES`, because the two pads compete for the same
# attention. Reaching the signature is worth a pad somewhat larger than the
# fixed one — but the reach reproduces EVERY intervening line, not just the
# signature, so at several times the fixed pad a hunk stops gaining context and
# starts being buried in an unrelated function body. Twice the largest fixed pad
# keeps the signature of a normal-sized function in view while bounding that.
_MAX_BOUNDARY_REACH = 2 * _MAX_CONTEXT_LINES


def expand_hunks(
    patch: str,
    file_content: str | None,
    n: int,
    after: int,
    boundaries: list[tuple[int, int]] | None = None,
) -> str:
    """Pad each hunk in *patch* with surrounding lines from *file_content*.

    Up to *n* lines are added before each hunk and up to *after* lines after it.
    The extra lines are drawn from the head-revision file text and rendered as
    normal unchanged context (space-prefixed), giving the model the function and
    definitions around a change. Hunk headers are rewritten so each hunk's
    start/length still describes the lines it now contains.

    ``boundaries`` (sorted 1-based inclusive ``(start, end)`` definition spans,
    from :func:`~lgtmaybe.engine.boundaries.definition_spans`) extends the
    LEADING pad up to the enclosing function/class signature when it sits above
    the fixed window and within :data:`_MAX_BOUNDARY_REACH` lines — the
    enclosing signature explains a change better than an arbitrary cut. Only a
    definition that still CONTAINS the hunk counts, so module-level code after a
    function is not padded back into it. A boundary can only ever widen the
    window, never shrink it.

    Hunks whose padded windows meet or overlap are **merged into one**, with the
    lines between them filled in as context. Padded independently they would
    each reach into the other's span, emitting a later hunk header that points
    above where the previous one ended — a non-monotonic patch that breaks the
    line arithmetic the model is asked to do, and that shows the same position
    twice with two different contents (once changed, once as stale context).

    This is best-effort: with ``n <= 0`` or no file content the patch is returned
    unchanged, and reads are clamped to the file's bounds. The result is for the
    model only — inline-comment positions are always computed from the real diff.
    """
    if n <= 0 or not file_content:
        return patch
    n_after = max(0, after)

    content_lines = file_content.splitlines()
    preamble, hunks = _parse_hunks(patch)
    if not hunks:
        return patch

    out = list(preamble)
    for group in _group_hunks(hunks, content_lines, n, n_after, boundaries):
        out.extend(_render_group(group, content_lines, n_after))
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class _Hunk:
    """One parsed hunk: its parsed header plus its verbatim body lines."""

    header: HunkHeader
    body: list[str]

    @property
    def last_new(self) -> int:
        """The hunk's final new-file line number."""
        return self.header.new_start + self.header.new_len - 1


def _parse_hunks(patch: str) -> tuple[list[str], list[_Hunk]]:
    """Split *patch* into its leading file-header lines and its hunks."""
    preamble: list[str] = []
    hunks: list[_Hunk] = []
    for line in patch.splitlines():
        header = parse_hunk_header(line)
        if header is not None:
            hunks.append(_Hunk(header, []))
        elif hunks:
            hunks[-1].body.append(line)
        else:
            preamble.append(line)
    return preamble, hunks


def _lead_start(hunk: _Hunk, n: int, boundaries: list[tuple[int, int]] | None) -> int:
    """The first new-file line *hunk*'s leading pad should reach back to."""
    lead_start = max(1, hunk.header.new_start - n)
    if boundaries:
        enclosing = _enclosing_boundary(boundaries, hunk.header.new_start)
        if enclosing is not None and enclosing < lead_start:
            # The enclosing definition starts above the fixed window and
            # within reach — widen the pad up to its signature line.
            lead_start = enclosing
    # The rewritten header's old start is `old_start - len(leading)`: when
    # earlier hunks net-added lines, old_start sits far below new_start, so an
    # unclamped pad drives it negative — an invalid header that
    # parse_hunk_header rejects, mis-numbering every line downstream. Clamp the
    # pad (after any boundary widening) so the old start stays >= 1.
    return max(lead_start, hunk.header.new_start - (hunk.header.old_start - 1))


@dataclass
class _Group:
    """A run of hunks rendered as one merged hunk, with its leading pad start."""

    hunks: list[_Hunk]
    lead_start: int


def _group_hunks(
    hunks: list[_Hunk],
    content_lines: list[str],
    n: int,
    n_after: int,
    boundaries: list[tuple[int, int]] | None,
) -> list[_Group]:
    """Group *hunks* into runs whose padded windows touch, in file order.

    A group is rendered as a single merged hunk. Two hunks join when the later
    one's leading pad reaches the earlier one's trailing pad (or the line just
    after it) — the point at which independent padding would double up.

    When they reach but the gap CANNOT be filled — head text shorter than the
    hunk positions, so merging would drop lines the merged header still claims —
    the later hunk instead starts its own group with its leading pad trimmed to
    clear the previous group. The pad is decoration; an overlapping header is
    not, and the non-overlap invariant has to hold in the degenerate case too.
    """
    groups: list[_Group] = []
    for hunk in hunks:
        lead_start = _lead_start(hunk, n, boundaries)
        if groups:
            previous = groups[-1].hunks[-1]
            trail_end = min(len(content_lines), previous.last_new + n_after)
            reaches = lead_start <= trail_end + 1
            fillable = hunk.header.new_start - 1 <= len(content_lines)
            if reaches and fillable:
                groups[-1].hunks.append(hunk)
                continue
            if reaches:
                lead_start = max(lead_start, trail_end + 1)
        groups.append(_Group(hunks=[hunk], lead_start=lead_start))
    return groups


def _render_group(group: _Group, content_lines: list[str], n_after: int) -> list[str]:
    """Render one group of hunks as a single padded hunk, header included.

    Header lengths are counted off the emitted body rather than derived
    arithmetically, so a merged hunk's counts cannot drift from what it holds.
    """
    first, last = group.hunks[0], group.hunks[-1]

    # Clamp reads to the file's real bounds: redaction or stale head text can
    # leave the file shorter than the hunk positions — degrade to less padding,
    # never an IndexError.
    lead_end = min(first.header.new_start, len(content_lines) + 1)
    leading = content_lines[min(group.lead_start, lead_end) - 1 : lead_end - 1]

    body: list[str] = []
    for index, hunk in enumerate(group.hunks):
        if index:
            # Fill the gap between the previous hunk's last line and this one
            # with the head text, as ordinary context.
            previous_end = group.hunks[index - 1].last_new
            body.extend(
                f" {text}" for text in content_lines[previous_end : hunk.header.new_start - 1]
            )
        body.extend(hunk.body)

    trailing = content_lines[last.last_new : min(len(content_lines), last.last_new + n_after)]

    lines = [f" {text}" for text in leading] + body + [f" {text}" for text in trailing]
    # An empty body line is git's rendering of an empty context line, so it
    # counts on both sides; "\ No newline at end of file" counts on neither.
    old_len = sum(1 for line in lines if line[:1] in {" ", "-", ""})
    new_len = sum(1 for line in lines if line[:1] in {" ", "+", ""})
    old_start = first.header.old_start - len(leading)
    new_start = first.header.new_start - len(leading)
    return [f"@@ -{old_start},{old_len} +{new_start},{new_len} @@{first.header.section}", *lines]
