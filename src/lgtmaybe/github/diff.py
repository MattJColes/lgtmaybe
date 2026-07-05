"""Diff utilities: commentable-line index and skip filter.

Commentable-line index: GitHub anchors a review comment with `line` + `side`
(`side="RIGHT"` → the new-file line number, `side="LEFT"` → the old-file line
number) rather than the deprecated, fragile `position` count. We build the set
of (filename, line, side) tuples a comment can legally attach to — every added,
deleted, or context line in the diff — so the gateway can post findings on real
diff lines and silently drop anything that isn't (e.g. a finding the model put
on an expanded-context-only line).

Skip filter: lockfiles, minified bundles, vendored/generated paths and binary
files are dropped before review to save tokens and avoid noise.
"""

from __future__ import annotations

from fnmatch import fnmatch

from lgtmaybe.core.diffparse import FILE_HEADER_RE, parse_hunk_header

# ---------------------------------------------------------------------------
# Commentable-line index
# ---------------------------------------------------------------------------

# The set of (filename, line, side) tuples a review comment can attach to, where
# `line` is the new-file line for side "RIGHT" and the old-file line for "LEFT".
CommentableLines = set[tuple[str, int, str]]


def build_commentable_lines(diff: str) -> CommentableLines:
    """Parse a unified diff into the set of commentable (file, line, side) tuples.

    GitHub anchors a review comment by file line and side, not by a running
    position count, so this avoids the off-by-N drift the `position` count
    suffered across multiple hunks. A line is commentable when it appears in the
    diff:

    - added ("+") lines → commentable on the **new** file (RIGHT) at new_line;
    - deleted ("-") lines → commentable on the **old** file (LEFT) at old_line;
    - context lines (no prefix or " " prefix) → commentable on **both** sides,
      at their new_line (RIGHT) and old_line (LEFT).

    Anything not in the returned set (out-of-diff or expanded-context-only
    lines) has no anchor and is dropped by the gateway rather than mis-posted.
    """
    commentable: CommentableLines = set()

    current_file: str | None = None
    new_line = 0  # current new-file line number
    old_line = 0  # current old-file line number
    in_hunk = False

    for raw_line in diff.splitlines():
        file_match = FILE_HEADER_RE.match(raw_line)
        if file_match:
            current_file = file_match.group(1)
            new_line = 0
            old_line = 0
            in_hunk = False
            continue

        if current_file is None:
            continue

        hunk = parse_hunk_header(raw_line)
        if hunk is not None:
            # Hunk header resets both line counters to the hunk's starts. No
            # position arithmetic — line/side bind directly to file lines.
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
            # Deleted line: present on the old side only.
            commentable.add((current_file, old_line, "LEFT"))
            old_line += 1
        elif raw_line.startswith("+"):
            # Added line: present on the new side only.
            commentable.add((current_file, new_line, "RIGHT"))
            new_line += 1
        else:
            # Context line (leading " " or empty): present on both sides.
            commentable.add((current_file, new_line, "RIGHT"))
            commentable.add((current_file, old_line, "LEFT"))
            new_line += 1
            old_line += 1

    return commentable


# ---------------------------------------------------------------------------
# Skip filter
# ---------------------------------------------------------------------------

_SKIP_FILENAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "npm-shrinkwrap.json",
        "Cargo.lock",
        "Gemfile.lock",
        "poetry.lock",
        "Pipfile.lock",
        "composer.lock",
        "go.sum",
        "uv.lock",
        "bun.lock",
        "bun.lockb",
        "deno.lock",
        "flake.lock",
        "mix.lock",
        "Package.resolved",
        "gradle.lockfile",
    }
)

_SKIP_DIR_PREFIXES = (
    "vendor/",
    "node_modules/",
    "dist/",
    "build/",
    ".git/",
    "third_party/",
    "third-party/",
)

# Glob patterns matched against the full path.
_SKIP_GLOB_PATTERNS = (
    "*.min.js",
    "*.min.css",
    "*.snap",
    "*.pb.go",
    "*.pb.py",
    "*.generated.*",
    "__generated__/*",
    "*.d.ts",
    # Sourcemaps are compiler output, never hand-written.
    "*.js.map",
    "*.css.map",
)

_SKIP_EXTENSIONS = frozenset(
    {
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".svg",
        ".webp",
        ".tiff",
        # Compiled / binary
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".a",
        ".o",
        ".obj",
        ".pyc",
        ".pyo",
        # Archives
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        # Media
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".avi",
        ".mov",
        ".mkv",
        # Docs / data blobs
        ".pdf",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        # Java/JVM
        ".class",
        ".jar",
        ".war",
    }
)


def is_reviewable(path: str) -> bool:
    """Return True if the file at *path* should be reviewed.

    Rejects lockfiles, minified files, vendored/generated directories, snapshot
    files, and binary extensions. Everything else passes through.
    """
    filename = path.rsplit("/", 1)[-1]

    if filename in _SKIP_FILENAMES:
        return False

    # Extension check
    dot = filename.rfind(".")
    if dot != -1:
        ext = filename[dot:].lower()
        if ext in _SKIP_EXTENSIONS:
            return False

    # Directory prefix check (path must start with one of the blocked dirs)
    for prefix in _SKIP_DIR_PREFIXES:
        if path.startswith(prefix) or ("/" + prefix) in path:
            return False

    # Glob pattern check on the full path
    for pattern in _SKIP_GLOB_PATTERNS:
        if fnmatch(path, pattern) or fnmatch(filename, pattern):
            return False

    return True
