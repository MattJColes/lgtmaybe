"""Building a unified diff from two file texts, for hosts that serve no diff.

Azure DevOps exposes a pull request's changed files and their contents but no
unified diff. Rather than teach the engine a second input shape, the adapter
renders the texts into the same ``git diff`` format every other forge returns,
so line numbers, the commentable-line index, and file splitting all run
through the one shared parser. These tests pin that round trip.
"""

from __future__ import annotations

import pytest

from lgtmaybe.core.diff import build_commentable_lines
from lgtmaybe.core.diffbuild import (
    binary_file_patch,
    file_patch,
    has_unsafe_path_chars,
    patch_from_hunks,
)
from lgtmaybe.core.diffparse import split_by_file, walk_diff


class TestFilePatch:
    def test_an_edit_round_trips_through_the_shared_parser(self) -> None:
        patch = file_patch(
            old_path="app.py",
            new_path="app.py",
            old_text="import os\n\ndef main():\n    pass\n",
            new_text='import os\npassword = "x"\n\ndef main():\n    pass\n',
        )

        assert patch.startswith("diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n")
        added = [(path, new) for path, kind, _old, new, _t in walk_diff(patch) if kind == "+"]
        assert added == [("app.py", 2)]
        assert ("app.py", 2, "RIGHT") in build_commentable_lines(patch)

    def test_line_numbers_match_the_file_far_from_the_top(self) -> None:
        """A hunk deep in a file must carry the real line number, not an offset."""
        old = "".join(f"line {i}\n" for i in range(1, 101))
        new = old.replace("line 80\n", "line 80 changed\n")
        patch = file_patch(old_path="f.txt", new_path="f.txt", old_text=old, new_text=new)

        changed = [
            (kind, o if kind == "-" else n)
            for _p, kind, o, n, _t in walk_diff(patch)
            if kind != " "
        ]
        assert changed == [("-", 80), ("+", 80)]

    def test_an_added_file_compares_against_dev_null(self) -> None:
        patch = file_patch(old_path=None, new_path="new.py", old_text="", new_text="a\nb\n")

        assert "new file mode 100644" in patch
        assert "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@" in patch
        assert split_by_file(patch, ["new.py"])[0][0] == "new.py"
        assert ("new.py", 2, "RIGHT") in build_commentable_lines(patch)

    def test_a_deleted_file_compares_against_dev_null(self) -> None:
        patch = file_patch(old_path="gone.py", new_path=None, old_text="a\nb\n", new_text="")

        assert "deleted file mode 100644" in patch
        assert "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@" in patch
        assert split_by_file(patch, ["gone.py"])[0][0] == "gone.py"
        assert ("gone.py", 1, "LEFT") in build_commentable_lines(patch)

    def test_a_rename_with_an_edit_anchors_on_the_new_path(self) -> None:
        patch = file_patch(
            old_path="old.py", new_path="new.py", old_text="a\nb\n", new_text="a\nc\n"
        )

        assert "rename from old.py\nrename to new.py" in patch
        assert split_by_file(patch, ["new.py"])[0][0] == "new.py"
        lines = build_commentable_lines(patch)
        assert ("new.py", 2, "RIGHT") in lines
        assert ("new.py", 2, "LEFT") in lines

    def test_a_pure_rename_has_no_hunks(self) -> None:
        patch = file_patch(old_path="old.py", new_path="new.py", old_text="a\n", new_text="a\n")

        assert patch == (
            "diff --git a/old.py b/new.py\nsimilarity index 100%\n"
            "rename from old.py\nrename to new.py\n"
        )
        assert split_by_file(patch, ["new.py"])[0][0] == "new.py"
        assert list(walk_diff(patch)) == []

    def test_content_that_looks_like_a_header_stays_content(self) -> None:
        """File text is untrusted; every content line carries a diff marker."""
        hostile = "diff --git a/x b/x\n+++ b/evil.py\n@@ -1 +1 @@\n"
        patch = file_patch(old_path=None, new_path="ok.py", old_text="", new_text=hostile)

        assert {path for path, *_ in walk_diff(patch)} == {"ok.py"}
        assert len(split_by_file(patch, ["ok.py"])) == 1

    def test_only_newlines_split_lines(self) -> None:
        """Hosts number lines by newline; form feeds must not add phantom lines."""
        patch = file_patch(old_path=None, new_path="f.txt", old_text="", new_text="a\fb\nc\n")
        added = [n for _p, kind, _o, n, _t in walk_diff(patch) if kind == "+"]
        assert added == [1, 2]

    def test_text_without_a_final_newline_keeps_its_last_line(self) -> None:
        patch = file_patch(old_path=None, new_path="f.txt", old_text="", new_text="a\nb")
        assert [t for *_rest, t in walk_diff(patch)] == ["a", "b"]


class TestBinaryFilePatch:
    def test_names_the_file_without_any_hunk(self) -> None:
        patch = binary_file_patch(old_path="logo.png", new_path="logo.png")

        assert patch == (
            "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
        )
        assert split_by_file(patch, ["logo.png"])[0][0] == "logo.png"
        assert list(walk_diff(patch)) == []

    def test_an_added_binary_compares_against_dev_null(self) -> None:
        patch = binary_file_patch(old_path=None, new_path="logo.png")
        assert "new file mode 100644" in patch
        assert "Binary files /dev/null and b/logo.png differ" in patch


class TestPatchFromHunks:
    """Wrapping hunks a host served readymade (GitLab's compare API) in a header."""

    def test_an_edit_round_trips_through_the_shared_parser(self) -> None:
        patch = patch_from_hunks(
            old_path="app.py", new_path="app.py", hunks="@@ -1,2 +1,3 @@\n import os\n+x = 1\n"
        )

        assert patch.startswith("diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n")
        added = [(path, new) for path, kind, _old, new, _t in walk_diff(patch) if kind == "+"]
        assert added == [("app.py", 2)]
        assert ("app.py", 2, "RIGHT") in build_commentable_lines(patch)

    def test_a_new_file_carries_its_served_mode_and_dev_null(self) -> None:
        patch = patch_from_hunks(
            old_path=None, new_path="run.sh", new_mode="100755", hunks="@@ -0,0 +1 @@\n+x\n"
        )

        assert "new file mode 100755\n--- /dev/null\n+++ b/run.sh\n" in patch

    def test_a_deleted_file_carries_its_served_mode_and_dev_null(self) -> None:
        patch = patch_from_hunks(
            old_path="gone.py", new_path=None, old_mode="100644", hunks="@@ -1 +0,0 @@\n-x\n"
        )

        assert "deleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n" in patch

    def test_a_rename_names_both_paths(self) -> None:
        patch = patch_from_hunks(
            old_path="was.py", new_path="moved.py", hunks="@@ -1 +1 @@\n-a\n+b\n"
        )

        assert "rename from was.py\nrename to moved.py\n--- a/was.py\n+++ b/moved.py\n" in patch

    def test_a_mode_change_without_hunks_is_header_only(self) -> None:
        patch = patch_from_hunks(
            old_path="run.sh", new_path="run.sh", old_mode="100644", new_mode="100755", hunks=""
        )

        assert patch == "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"

    def test_hunks_without_a_final_newline_gain_one(self) -> None:
        assert patch_from_hunks(
            old_path="a.py", new_path="a.py", hunks="@@ -1 +1 @@\n-a\n+b"
        ).endswith("+b\n")


class TestUnsafePaths:
    @pytest.mark.parametrize("path", ["a\nb.py", "a\rb.py", "a\tb.py", "a\x00b", "a\x7fb"])
    def test_control_characters_are_unsafe(self, path: str) -> None:
        """A newline in a filename could forge diff headers, so it is refused."""
        assert has_unsafe_path_chars(path)

    @pytest.mark.parametrize("path", ["src/app.py", "docs/my file.md", "src/café.py"])
    def test_ordinary_paths_are_safe(self, path: str) -> None:
        assert not has_unsafe_path_chars(path)
