"""Token-aware patch fitting.

Splits changed files into batches that each fit within a token budget.
Provides a dynamic context-line calculator for the remaining budget.
"""

from __future__ import annotations

from bisect import bisect_right
from functools import lru_cache
from typing import Any

from lgtmaybe.core.diffparse import parse_hunk_header

_MAX_CONTEXT_LINES = 20
_MIN_CONTEXT_LINES = 0
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


def count_tokens(text: str) -> int:
    """Return the token count for *text* using tiktoken, with a len/4 fallback."""
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
    if not files:
        return []

    # RLM walk: decompose any over-budget file into per-hunk units up front, so a
    # large file becomes several small calls that each fit instead of one
    # oversized call. Files within budget pass through whole (context preserved).
    if recursive:
        units: list[tuple[str, str]] = []
        for path, patch in files:
            if count_tokens(patch) >= max_tokens:
                hunks = split_patch_into_hunks(patch)
                if len(hunks) > 1:
                    units.extend((path, hunk) for hunk in hunks)
                    continue
            units.append((path, patch))
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
    if remaining_tokens <= 0:
        return _MIN_CONTEXT_LINES

    # Scale: every _SCALE remaining tokens buys one context line,
    # up to _MAX_CONTEXT_LINES.
    lines = remaining_tokens // _SCALE
    return min(int(lines), _MAX_CONTEXT_LINES)


def _enclosing_boundary(boundaries: list[int], new_start: int) -> int | None:
    """The nearest definition start at or above *new_start*, if within reach.

    *boundaries* is sorted ascending; the enclosing candidate is the last one
    ``<= new_start``. None when there is none, or when it sits more than
    :data:`_MAX_BOUNDARY_REACH` lines above (padding to it would drown the diff).
    """
    idx = bisect_right(boundaries, new_start) - 1
    if idx < 0:
        return None
    candidate = boundaries[idx]
    if new_start - candidate > _MAX_BOUNDARY_REACH:
        return None
    return candidate


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
_MAX_BOUNDARY_REACH = 120


def expand_hunks(
    patch: str,
    file_content: str | None,
    n: int,
    after: int | None = None,
    boundaries: list[int] | None = None,
) -> str:
    """Pad each hunk in *patch* with surrounding lines from *file_content*.

    Up to *n* lines are added before each hunk and up to *after* lines after it
    (``after=None`` keeps the original symmetric contract: *n* on both sides).
    The extra lines are drawn from the head-revision file text and rendered as
    normal unchanged context (space-prefixed), giving the model the function and
    definitions around a change. Hunk headers are rewritten so each hunk's
    start/length still describes the lines it now contains.

    ``boundaries`` (sorted 1-based definition start lines, from
    :func:`~lgtmaybe.engine.boundaries.definition_starts`) extends the LEADING
    pad up to the enclosing function/class signature when it sits above the
    fixed window and within :data:`_MAX_BOUNDARY_REACH` lines — the enclosing
    signature explains a change better than an arbitrary cut. A boundary can
    only ever widen the window, never shrink it.

    This is best-effort: with ``n <= 0`` or no file content the patch is returned
    unchanged, and reads are clamped to the file's bounds. The result is for the
    model only — inline-comment positions are always computed from the real diff.
    """
    if n <= 0 or not file_content:
        return patch
    n_after = n if after is None else max(0, after)

    content_lines = file_content.splitlines()
    out: list[str] = []
    pending_trailing: list[str] = []

    for line in patch.splitlines():
        header = parse_hunk_header(line)
        if header is None:
            out.append(line)
            continue

        # A new hunk starts: flush the previous hunk's trailing context first.
        out.extend(f" {text}" for text in pending_trailing)

        old_start = header.old_start
        old_len = header.old_len
        new_start = header.new_start
        new_len = header.new_len
        section = header.section

        # Lines immediately before the hunk and after its last new-file line.
        lead_start = max(1, new_start - n)
        if boundaries:
            enclosing = _enclosing_boundary(boundaries, new_start)
            if enclosing is not None and enclosing < lead_start:
                # The enclosing definition starts above the fixed window and
                # within reach — widen the pad up to its signature line.
                lead_start = enclosing
        leading = [content_lines[i - 1] for i in range(lead_start, new_start)]
        last_new = new_start + new_len - 1
        trailing = [
            content_lines[i - 1]
            for i in range(last_new + 1, min(len(content_lines), last_new + n_after) + 1)
        ]
        pending_trailing = trailing

        pad = len(leading) + len(trailing)
        out.append(
            f"@@ -{old_start - len(leading)},{old_len + pad} "
            f"+{new_start - len(leading)},{new_len + pad} @@{section}"
        )
        out.extend(f" {text}" for text in leading)

    out.extend(f" {text}" for text in pending_trailing)
    return "\n".join(out) + "\n"
