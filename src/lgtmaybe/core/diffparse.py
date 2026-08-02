"""Unified-diff parsing primitives.

One home for the regexes and helpers that read a ``git diff``: splitting a diff
into per-file patches and parsing hunk headers. Shared by the engine (batching,
hunk expansion) and the github adapter (commentable-line index) so the patterns and their
off-by-one rules live in exactly one place.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

# "diff --git a/<old> b/<new>" — capture the new-side path. MULTILINE so it can
# be used both with finditer over a whole diff and with match on a single line.
FILE_HEADER_RE = re.compile(r"^diff --git a/.+ b/(.+)$", re.MULTILINE)

# "@@ -old_start[,old_len] +new_start[,new_len] @@[ section]"
HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass(frozen=True)
class HunkHeader:
    """The parsed numbers from a unified-diff hunk header.

    Lengths default to 1 when omitted (``@@ -3 +4 @@``), matching the diff spec.
    """

    old_start: int
    old_len: int
    new_start: int
    new_len: int
    section: str


def parse_hunk_header(line: str) -> HunkHeader | None:
    """Parse a hunk-header *line* into a HunkHeader, or None if it isn't one."""
    m = HUNK_HEADER_RE.match(line)
    if m is None:
        return None
    return HunkHeader(
        old_start=int(m.group(1)),
        old_len=int(m.group(2)) if m.group(2) is not None else 1,
        new_start=int(m.group(3)),
        new_len=int(m.group(4)) if m.group(4) is not None else 1,
        section=m.group(5),
    )


def walk_diff(diff: str) -> Iterator[tuple[str, str, int, int, str]]:
    """Yield ``(path, kind, old_line, new_line, text)`` per in-hunk line.

    The one home for the diff walk's off-by-one rules, so every consumer counts
    lines identically. ``kind`` is the line's marker — ``"-"`` deleted, ``"+"``
    added, ``" "`` context — and ``text`` is the line with that marker stripped.
    ``old_line`` / ``new_line`` are the line's number in the old / new file at
    the point it is yielded; only the sides the line exists on are meaningful
    (a deleted line has no new-file line, an added line no old-file line), and
    the counters advance only for the sides the line occupies.

    Lines outside a hunk (file/index headers, ``---``/``+++`` preamble) and the
    ``\\ No newline at end of file`` marker are not diff content and are skipped.
    """
    current_file: str | None = None
    new_line = 0
    old_line = 0
    in_hunk = False

    for raw_line in diff.splitlines():
        file_match = FILE_HEADER_RE.match(raw_line)
        if file_match:
            current_file = file_match.group(1)
            in_hunk = False
            continue
        if current_file is None:
            continue
        hunk = parse_hunk_header(raw_line)
        if hunk is not None:
            # Hunk header resets both counters to the hunk's starts. No position
            # arithmetic — line/side bind directly to file lines.
            new_line = hunk.new_start
            old_line = hunk.old_start
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw_line.startswith("\\"):
            # "\ No newline at end of file" — a diff marker, not a real line. It
            # must not advance either counter or every later line shifts by one.
            continue
        if raw_line.startswith("-"):
            yield current_file, "-", old_line, new_line, raw_line[1:]
            old_line += 1
        elif raw_line.startswith("+"):
            yield current_file, "+", old_line, new_line, raw_line[1:]
            new_line += 1
        else:  # context line (leading " " or empty): advances both sides
            yield current_file, " ", old_line, new_line, raw_line[1:]
            new_line += 1
            old_line += 1


def changed_line_index(diff: str) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """Map ``(path, side)`` → ordered ``(line_number, text)`` for each changed line.

    ``side`` is ``"RIGHT"`` for added (``+``) lines at their new-file line number
    and ``"LEFT"`` for deleted (``-``) lines at their old-file line number — the
    same coordinates GitHub anchors a review comment by. ``text`` is the line
    content with the ``+``/``-`` marker stripped (surrounding whitespace kept).

    Used to re-anchor a finding whose model-counted ``line`` drifted: the model
    returns the verbatim flagged line, which is matched back to the real changed
    line here. Context (unchanged) lines are skipped — a finding always anchors
    on a changed line.
    """
    index: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for path, kind, old_line, new_line, text in walk_diff(diff):
        if kind == "-":
            index.setdefault((path, "LEFT"), []).append((old_line, text))
        elif kind == "+":
            index.setdefault((path, "RIGHT"), []).append((new_line, text))
        # context lines advance both counters but anchor nothing
    return index


def hunk_for_line(diff: str, path: str, line: int, side: str = "RIGHT") -> str | None:
    """Return the single hunk (with its file header) covering ``(path, line, side)``.

    ``line`` is the new-file line for side ``"RIGHT"`` and the old-file line for
    ``"LEFT"`` — the same coordinates a review comment anchors by. Coverage uses
    the hunk header's ranges (``new_start..new_start+new_len-1`` on the right,
    the old counterpart on the left), so the first hunk whose range contains
    ``line`` is returned verbatim, prefixed with the file's diff header.

    Returns ``None`` when *path* is not in the diff or no hunk covers the line
    (e.g. a comment on a line outside the current diff). Used to give a
    finding-thread reply the surrounding changed code as grounding context.
    """
    for patch_path, patch in split_by_file(diff, [path]):
        if patch_path != path:
            continue
        lines = patch.splitlines()
        hunk_starts = [i for i, raw in enumerate(lines) if parse_hunk_header(raw) is not None]
        if not hunk_starts:
            return None
        header = lines[: hunk_starts[0]]
        for idx, start in enumerate(hunk_starts):
            end = hunk_starts[idx + 1] if idx + 1 < len(hunk_starts) else len(lines)
            hunk = parse_hunk_header(lines[start])
            assert hunk is not None  # guaranteed by hunk_starts membership
            if side == "LEFT":
                lo, hi = hunk.old_start, hunk.old_start + hunk.old_len - 1
            else:
                lo, hi = hunk.new_start, hunk.new_start + hunk.new_len - 1
            if lo <= line <= hi:
                return "\n".join(header + lines[start:end])
        return None
    return None


def split_by_file(diff: str, changed_files: list[str]) -> list[tuple[str, str]]:
    """Split a unified diff into per-file ``(path, patch)`` pairs.

    Each patch runs from its ``diff --git`` header to the next one. When there
    are no headers the whole diff is treated as one patch, associated with the
    first changed file (or ``"unknown"`` when none is given).
    """
    matches = list(FILE_HEADER_RE.finditer(diff))
    if not matches:
        path = changed_files[0] if changed_files else "unknown"
        return [(path, diff)]

    result: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        path = match.group(1)
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(diff)
        result.append((path, diff[start:end]))
    return result
