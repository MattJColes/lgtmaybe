"""Unified-diff parsing primitives.

One home for the regexes and helpers that read a ``git diff``: splitting a diff
into per-file patches and parsing hunk headers. Shared by the engine (batching,
hunk expansion) and the github adapter (commentable-line index) so the patterns and their
off-by-one rules live in exactly one place.
"""

from __future__ import annotations

import re
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
            index.setdefault((current_file, "LEFT"), []).append((old_line, raw_line[1:]))
            old_line += 1
        elif raw_line.startswith("+"):
            index.setdefault((current_file, "RIGHT"), []).append((new_line, raw_line[1:]))
            new_line += 1
        else:  # context line: advances both sides, anchors nothing
            new_line += 1
            old_line += 1
    return index


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
