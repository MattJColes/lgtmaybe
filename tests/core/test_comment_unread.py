"""The summary section naming files an adapter could not read.

A host that serves no diff (Azure DevOps) fetches each file itself. A file that
will not come back is shown to the model as changed but not shown; the summary
has to say so too, or a reader takes the review as covering it.
"""

from __future__ import annotations

from lgtmaybe.core.comment import render_unread_files


def test_nothing_unread_renders_nothing() -> None:
    assert render_unread_files([]) == ""


def test_names_each_file_once_in_order() -> None:
    body = render_unread_files(["b.py", "a.py", "b.py"])

    assert "could not be read" in body
    assert body.index("`b.py`") < body.index("`a.py`")
    assert body.count("`b.py`") == 1


def test_backticks_in_a_path_cannot_break_out_of_inline_code() -> None:
    """Paths are chosen by the PR author."""
    body = render_unread_files(["evil`](https://x.example)`.py"])

    assert body.count("`") == 2, "only the code span's own delimiters survive"
    assert "`evil'](https://x.example)'.py`" in body
