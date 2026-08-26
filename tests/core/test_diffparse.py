"""Shared unified-diff parsing primitives."""

from __future__ import annotations

from lgtmaybe.core.diffparse import (
    changed_line_count,
    hunk_for_line,
    parse_hunk_header,
    split_by_file,
    walk_diff,
)

_TWO_FILE_DIFF = """\
diff --git a/src/a.py b/src/a.py
index 111..222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,3 @@
 x = 1
+y = 2
 z = 3
diff --git a/src/b.py b/src/b.py
index 333..444 100644
--- a/src/b.py
+++ b/src/b.py
@@ -10 +10 @@
-old
+new
"""


class TestSplitByFile:
    def test_splits_into_one_patch_per_file(self):
        parts = split_by_file(_TWO_FILE_DIFF, ["src/a.py", "src/b.py"])
        paths = [path for path, _ in parts]
        assert paths == ["src/a.py", "src/b.py"]

    def test_each_patch_keeps_its_own_header_and_hunk(self):
        parts = dict(split_by_file(_TWO_FILE_DIFF, []))
        assert "+y = 2" in parts["src/a.py"]
        assert "+y = 2" not in parts["src/b.py"]
        assert "+new" in parts["src/b.py"]

    def test_no_headers_falls_back_to_first_changed_file(self):
        parts = split_by_file("@@ -1 +1 @@\n-a\n+b\n", ["only.py"])
        assert parts == [("only.py", "@@ -1 +1 @@\n-a\n+b\n")]

    def test_no_headers_and_no_files_uses_unknown(self):
        parts = split_by_file("just text", [])
        assert parts == [("unknown", "just text")]

    def test_preserves_a_new_path_containing_the_header_delimiter(self):
        diff = (
            "diff --git a/dir b/file.py b/dir b/file.py\n"
            "--- a/dir b/file.py\t\n"
            "+++ b/dir b/file.py\t\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )

        assert split_by_file(diff, ["dir b/file.py"])[0][0] == "dir b/file.py"


class TestParseHunkHeader:
    def test_parses_full_header_with_lengths_and_section(self):
        h = parse_hunk_header("@@ -1,2 +3,4 @@ def foo():")
        assert h is not None
        assert (h.old_start, h.old_len, h.new_start, h.new_len) == (1, 2, 3, 4)
        assert h.section == " def foo():"

    def test_omitted_lengths_default_to_one(self):
        h = parse_hunk_header("@@ -10 +20 @@")
        assert h is not None
        assert (h.old_start, h.old_len, h.new_start, h.new_len) == (10, 1, 20, 1)

    def test_non_hunk_line_returns_none(self):
        assert parse_hunk_header(" context line") is None
        assert parse_hunk_header("diff --git a/x b/x") is None


_MULTI_HUNK_DIFF = """\
diff --git a/src/a.py b/src/a.py
index 111..222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,3 @@
 x = 1
+y = 2
 z = 3
@@ -20,2 +21,3 @@
 p = 1
+q = 2
 r = 3
"""


class TestHunkForLine:
    def test_returns_the_hunk_covering_a_right_side_line(self):
        # The added line "q = 2" is new-file line 22, inside the second hunk.
        hunk = hunk_for_line(_MULTI_HUNK_DIFF, "src/a.py", 22, "RIGHT")
        assert hunk is not None
        assert "+q = 2" in hunk
        assert "+y = 2" not in hunk  # not the first hunk
        # Carries the file header so a reply's context still names the file.
        assert "+++ b/src/a.py" in hunk

    def test_picks_the_first_hunk_for_a_line_it_covers(self):
        hunk = hunk_for_line(_MULTI_HUNK_DIFF, "src/a.py", 2, "RIGHT")
        assert hunk is not None
        assert "+y = 2" in hunk
        assert "+q = 2" not in hunk

    def test_none_when_line_is_outside_every_hunk(self):
        assert hunk_for_line(_MULTI_HUNK_DIFF, "src/a.py", 999, "RIGHT") is None

    def test_none_when_path_not_in_diff(self):
        assert hunk_for_line(_MULTI_HUNK_DIFF, "other.py", 2, "RIGHT") is None

    def test_matches_a_left_side_line_by_old_file_number(self):
        hunk = hunk_for_line(_TWO_FILE_DIFF, "src/b.py", 10, "LEFT")
        assert hunk is not None
        assert "-old" in hunk


class TestWalkDiff:
    def test_yields_kind_and_both_line_numbers_per_in_hunk_line(self):
        assert list(walk_diff(_TWO_FILE_DIFF)) == [
            ("src/a.py", " ", 1, 1, "x = 1"),
            ("src/a.py", "+", 2, 2, "y = 2"),
            ("src/a.py", " ", 2, 3, "z = 3"),
            ("src/b.py", "-", 10, 10, "old"),
            ("src/b.py", "+", 11, 10, "new"),
        ]

    def test_skips_everything_outside_a_hunk(self):
        # Headers, index lines and ---/+++ preamble are not diff content.
        assert [text for *_, text in walk_diff(_TWO_FILE_DIFF)] == [
            "x = 1",
            "y = 2",
            "z = 3",
            "old",
            "new",
        ]

    def test_no_newline_marker_is_not_a_line(self):
        diff = (
            "diff --git a/f.txt b/f.txt\n"
            "@@ -1,1 +1,1 @@\n"
            "-old line\n"
            "\\ No newline at end of file\n"
            "+new line\n"
            "\\ No newline at end of file\n"
        )
        assert list(walk_diff(diff)) == [
            ("f.txt", "-", 1, 1, "old line"),
            ("f.txt", "+", 2, 1, "new line"),
        ]

    def test_preserves_a_walked_path_containing_the_header_delimiter(self):
        diff = (
            "diff --git a/dir b/file.py b/dir b/file.py\n"
            "--- a/dir b/file.py\t\n"
            "+++ b/dir b/file.py\t\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )

        assert {path for path, *_ in walk_diff(diff)} == {"dir b/file.py"}


class TestChangedLineIndex:
    def test_indexes_added_line_on_right_at_new_line(self):
        from lgtmaybe.core.diffparse import changed_line_index

        index = changed_line_index(_TWO_FILE_DIFF)
        assert index[("src/a.py", "RIGHT")] == [(2, "y = 2")]

    def test_indexes_a_modify_pair_on_both_sides(self):
        from lgtmaybe.core.diffparse import changed_line_index

        index = changed_line_index(_TWO_FILE_DIFF)
        assert index[("src/b.py", "LEFT")] == [(10, "old")]
        assert index[("src/b.py", "RIGHT")] == [(10, "new")]

    def test_context_lines_are_not_indexed(self):
        from lgtmaybe.core.diffparse import changed_line_index

        index = changed_line_index(_TWO_FILE_DIFF)
        right = index[("src/a.py", "RIGHT")]
        assert all(text != "x = 1" and text != "z = 3" for _, text in right)

    def test_no_newline_marker_does_not_shift_later_lines(self):
        from lgtmaybe.core.diffparse import changed_line_index

        # git emits "\ No newline at end of file" after a +/- line for files
        # without a trailing newline. It must not advance the line counters.
        diff = (
            "diff --git a/f.txt b/f.txt\n"
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@ -1,2 +1,2 @@\n"
            " context line\n"
            "-old line\n"
            "\\ No newline at end of file\n"
            "+new line\n"
            "\\ No newline at end of file\n"
        )
        index = changed_line_index(diff)
        # context is line 1, so the added line is the new-file line 2 (not 3).
        assert index[("f.txt", "RIGHT")] == [(2, "new line")]
        assert index[("f.txt", "LEFT")] == [(2, "old line")]


class TestChangedLineCount:
    def test_counts_added_and_removed_lines(self):
        assert changed_line_count(_TWO_FILE_DIFF) == 3

    def test_file_headers_are_not_changed_lines(self):
        # The `---`/`+++` pair every per-file patch carries is diff metadata,
        # not changed code — counting it inflates every file by two.
        patch = (
            "diff --git a/a.py b/a.py\n"
            "index 111..222 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert changed_line_count(patch) == 2

    def test_context_and_metadata_lines_are_ignored(self):
        assert changed_line_count("diff --git a/a.py b/a.py\n@@ -1 +1 @@\n unchanged\n") == 0

    def test_changed_lines_whose_content_looks_like_a_header_still_count(self):
        # A line whose own content starts with `++` renders as `+++ ...` inside
        # the hunk. Excluding by prefix would undercount it, letting a large
        # patch duck the triage escalation floor.
        patch = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "--- leading dashes\n"
            "+++ leading pluses\n"
        )
        assert changed_line_count(patch) == 2
