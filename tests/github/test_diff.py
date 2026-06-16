"""Tests for the diff commentable-line index and skip filter."""

from __future__ import annotations

from lgtmaybe.github import build_commentable_lines, is_reviewable

# ---------------------------------------------------------------------------
# Commentable-line index
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 0000001..0000002 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,6 @@
 import os
+import sys

 def main():
-    pass
+    print("hello")
+    return 0
diff --git a/src/utils.py b/src/utils.py
index 0000003..0000004 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -10,3 +10,4 @@ def helper():
     x = 1
     y = 2
     return x + y
+    # comment added
"""


def test_added_line_is_commentable_on_right() -> None:
    """An added line is commentable on RIGHT at its new-file line number."""
    index = build_commentable_lines(SAMPLE_DIFF)
    # "+import sys" is the first added line in src/app.py, at new-file line 2.
    assert ("src/app.py", 2, "RIGHT") in index
    # "+    print(...)" / "+    return 0" land at new-file lines 5 and 6.
    assert ("src/app.py", 5, "RIGHT") in index
    assert ("src/app.py", 6, "RIGHT") in index


def test_context_line_is_commentable_on_both_sides() -> None:
    """A context line is commentable on RIGHT (new line) and LEFT (old line)."""
    index = build_commentable_lines(SAMPLE_DIFF)
    # "import os" is context at new-file line 1 / old-file line 1.
    assert ("src/app.py", 1, "RIGHT") in index
    assert ("src/app.py", 1, "LEFT") in index


def test_deleted_line_is_commentable_on_left() -> None:
    """A deleted line is commentable on LEFT at its old-file line number."""
    index = build_commentable_lines(SAMPLE_DIFF)
    # "-    pass" was old-file line 4 (context "import os", blank, "def main():"
    # are old lines 1-3); it is anchored on the LEFT side only.
    assert ("src/app.py", 4, "LEFT") in index


def test_line_not_in_diff_is_absent() -> None:
    """A line number outside any hunk is not commentable on either side."""
    index = build_commentable_lines(SAMPLE_DIFF)
    assert ("src/app.py", 999, "RIGHT") not in index
    assert ("src/app.py", 999, "LEFT") not in index


def test_unknown_file_is_absent() -> None:
    """A file not present in the diff is never commentable."""
    index = build_commentable_lines(SAMPLE_DIFF)
    assert ("totally_absent.py", 1, "RIGHT") not in index


def test_line_counting_resets_per_file() -> None:
    """Line tracking resets per file, not globally."""
    index = build_commentable_lines(SAMPLE_DIFF)
    # src/utils.py: hunk @@ -10,3 +10,4 @@ → "    x = 1" is new-file line 10.
    assert ("src/utils.py", 10, "RIGHT") in index
    # The trailing "+    # comment added" is the added line at new-file line 13.
    assert ("src/utils.py", 13, "RIGHT") in index


# ---------------------------------------------------------------------------
# Skip filter
# ---------------------------------------------------------------------------

MIXED_FILE_LIST = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "app.min.js",
    "styles.min.css",
    "vendor/lib.py",
    "node_modules/dep/index.js",
    "dist/bundle.js",
    "src/__snapshots__/app.test.js.snap",
    "image.png",
    "binary.exe",
    "src/app.py",
    "src/models.py",
]


def test_is_reviewable_rejects_lockfiles() -> None:
    assert not is_reviewable("package-lock.json")
    assert not is_reviewable("yarn.lock")
    assert not is_reviewable("pnpm-lock.yaml")


def test_is_reviewable_rejects_minified() -> None:
    assert not is_reviewable("app.min.js")
    assert not is_reviewable("styles.min.css")


def test_is_reviewable_rejects_vendor_and_generated_dirs() -> None:
    assert not is_reviewable("vendor/lib.py")
    assert not is_reviewable("node_modules/dep/index.js")
    assert not is_reviewable("dist/bundle.js")


def test_is_reviewable_rejects_snapshots() -> None:
    assert not is_reviewable("src/__snapshots__/app.test.js.snap")


def test_is_reviewable_rejects_binary_extensions() -> None:
    assert not is_reviewable("image.png")
    assert not is_reviewable("binary.exe")


def test_is_reviewable_accepts_source_files() -> None:
    assert is_reviewable("src/app.py")
    assert is_reviewable("src/models.py")


def test_filter_mixed_list_leaves_only_source() -> None:
    """Given a mixed file list, only source files survive."""
    reviewable = [f for f in MIXED_FILE_LIST if is_reviewable(f)]
    assert reviewable == ["src/app.py", "src/models.py"]


def test_is_reviewable_rejects_uppercase_binary_extensions() -> None:
    """Extension matching is case-insensitive — uppercase binaries still skip."""
    assert not is_reviewable("Logo.PNG")
    assert not is_reviewable("Photo.JPG")
    assert not is_reviewable("lib.SO")


def test_is_reviewable_rejects_nested_vendored_paths() -> None:
    """A blocked directory anywhere in the path (not just the prefix) is skipped."""
    assert not is_reviewable("packages/web/node_modules/dep/index.js")
    assert not is_reviewable("services/api/vendor/pkg.go")
    assert not is_reviewable("a/b/dist/bundle.js")


def test_is_reviewable_rejects_more_lockfiles() -> None:
    assert not is_reviewable("go.sum")
    assert not is_reviewable("Cargo.lock")
    assert not is_reviewable("poetry.lock")
    assert not is_reviewable("composer.lock")


def test_is_reviewable_rejects_generated_globs() -> None:
    assert not is_reviewable("api/service.pb.go")
    assert not is_reviewable("types/models.d.ts")
    assert not is_reviewable("schema.generated.ts")
    assert not is_reviewable("__generated__/foo.py")


def test_is_reviewable_accepts_dotfiles_and_configs() -> None:
    """Config/source-ish files that are not on a skip list should be reviewed."""
    assert is_reviewable(".github/workflows/ci.yml")
    assert is_reviewable("Dockerfile")
    assert is_reviewable("src/auth/login.py")


# ---------------------------------------------------------------------------
# Commentable lines — multi-hunk and add-after-delete anchoring
# ---------------------------------------------------------------------------

_MULTI_HUNK_DIFF = """\
diff --git a/src/svc.py b/src/svc.py
index 0000001..0000002 100644
--- a/src/svc.py
+++ b/src/svc.py
@@ -1,3 +1,4 @@
 import os
-import sys
+import sys
+import json
@@ -20,2 +21,3 @@ def handler():
     run()
+    cleanup()
"""


def test_second_hunk_line_anchors_to_its_real_new_file_line() -> None:
    """Findings in the 2nd hunk anchor to their true new-file line, not an
    off-by-N diff position.

    Regression for the multi-hunk bug: the old `position` math did not count the
    intervening "@@" hunk header, so a finding on the 2nd hunk's "+    cleanup()"
    (new-file line 22) was posted one line too high. With line+side the anchor is
    simply the new-file line, independent of any earlier hunk.
    """
    index = build_commentable_lines(_MULTI_HUNK_DIFF)
    # Second hunk @@ -20,2 +21,3 @@: "    run()" is context at new-file line 21,
    # "+    cleanup()" is added at new-file line 22.
    assert ("src/svc.py", 21, "RIGHT") in index
    assert ("src/svc.py", 22, "RIGHT") in index


def test_added_line_after_deletion_anchors_correctly() -> None:
    index = build_commentable_lines(_MULTI_HUNK_DIFF)
    # First hunk: "import os" context = new-line 1; "-import sys" del = old-line 2
    # (LEFT, no new line); "+import sys" add = new-line 2; "+import json" = new-line 3.
    assert ("src/svc.py", 1, "RIGHT") in index
    assert ("src/svc.py", 2, "LEFT") in index
    assert ("src/svc.py", 2, "RIGHT") in index
    assert ("src/svc.py", 3, "RIGHT") in index
