"""Rendering two file texts as a ``git diff`` patch.

Most forges serve a unified diff, and the whole pipeline reads that one format:
``core.diffparse`` walks it, ``core.diff`` indexes its commentable lines, and the
engine splits and batches it. A host that serves only file contents (Azure
DevOps has no unified-diff endpoint) therefore renders one here rather than
teaching every consumer a second input shape. Host-neutral, so it lives in
``core`` beside the parser it has to agree with.

Both texts are untrusted (they come from the change under review). That is safe
by construction: every content line is emitted behind a ``+``, ``-``, or space
marker, so no file content can start a line that the parser would read as a
file or hunk header. Paths are the remaining way in, and the adapter refuses
any carrying a control character (``has_unsafe_path_chars``) before calling in.
"""

from __future__ import annotations

import difflib

# Lines of unchanged context around each hunk, matching ``git diff``'s default.
_CONTEXT = 3

# Every C0 control character plus DEL. A newline in a filename would let a path
# forge a header line; a tab would be cut off by the header regexes.
_UNSAFE_PATH_CHARS = frozenset({*map(chr, range(0x20)), "\x7f"})


def has_unsafe_path_chars(path: str) -> bool:
    """Whether ``path`` carries a character a diff header cannot represent safely.

    ``git diff`` C-quotes such paths; a rendered diff that did not would let a
    filename chosen by the PR author inject header lines. Callers drop these
    files rather than quote them, since the shared parser does not unquote.
    """
    return any(char in _UNSAFE_PATH_CHARS for char in path)


def _lines(text: str) -> list[str]:
    """Split on ``\\n`` only, as hosts number lines (``str.splitlines`` also splits
    on form feeds and Unicode separators, which would shift every later line)."""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def _header(old_path: str | None, new_path: str | None) -> list[str]:
    """The ``diff --git`` line plus the add/delete/rename metadata git emits."""
    old = old_path if old_path is not None else new_path
    new = new_path if new_path is not None else old_path
    lines = [f"diff --git a/{old} b/{new}"]
    if old_path is None:
        lines.append("new file mode 100644")
    elif new_path is None:
        lines.append("deleted file mode 100644")
    elif old_path != new_path:
        lines += [f"rename from {old_path}", f"rename to {new_path}"]
    return lines


def file_patch(
    *,
    old_path: str | None,
    new_path: str | None,
    old_text: str,
    new_text: str,
) -> str:
    """One file's ``git diff`` patch, from its text before and after the change.

    ``old_path`` is None for an added file and ``new_path`` None for a deleted
    one; the missing side compares against ``/dev/null`` with empty text. A
    rename with identical text has no hunks, and like git it then carries no
    ``---``/``+++`` lines either.
    """
    if old_path is None and new_path is None:
        raise ValueError("a file patch needs at least one path")
    header = _header(old_path, new_path)
    hunks = list(difflib.unified_diff(_lines(old_text), _lines(new_text), n=_CONTEXT, lineterm=""))[
        2:
    ]  # difflib's own ---/+++ lines; ours carry the a/ b/ prefixes git uses
    if not hunks:
        if old_path is not None and new_path is not None and old_path != new_path:
            header.insert(1, "similarity index 100%")
        return "\n".join(header) + "\n"
    old_label = f"a/{old_path}" if old_path is not None else "/dev/null"
    new_label = f"b/{new_path}" if new_path is not None else "/dev/null"
    return "\n".join([*header, f"--- {old_label}", f"+++ {new_label}", *hunks]) + "\n"


def binary_file_patch(*, old_path: str | None, new_path: str | None) -> str:
    """A hunkless patch naming a file whose content is not shown.

    The shape ``git diff`` uses for binary files, so the engine's existing
    handling applies unchanged: the file is listed as changed, but there is no
    line a finding could be anchored to.
    """
    if old_path is None and new_path is None:
        raise ValueError("a file patch needs at least one path")
    old_label = f"a/{old_path}" if old_path is not None else "/dev/null"
    new_label = f"b/{new_path}" if new_path is not None else "/dev/null"
    lines = [*_header(old_path, new_path), f"Binary files {old_label} and {new_label} differ"]
    return "\n".join(lines) + "\n"
