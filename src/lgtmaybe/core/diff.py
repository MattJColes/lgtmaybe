"""Diff utilities: commentable-line index and skip filter.

Host-neutral, and in ``core`` for that reason: every forge lgtmaybe posts to
anchors a comment to a line of a unified diff, and every one of them wants the
same files skipped. A forge adapter translates these tuples into its own
position vocabulary; none of them owns the parsing.

Commentable-line index: a review comment is anchored with `line` + `side`
(`side="RIGHT"` → the new-file line number, `side="LEFT"` → the old-file line
number) rather than the deprecated, fragile `position` count. We build the set
of (filename, line, side) tuples a comment can legally attach to — every added,
deleted, or context line in the diff — so the gateway can post findings on real
diff lines and silently drop anything that isn't (e.g. a finding the model put
on an expanded-context-only line).

Skip filter: lockfiles, minified bundles, vendored/generated paths, generated
LLM-index corpora (llms.txt / llms-full.txt), and binary files are dropped before
review to save tokens and avoid noise.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from lgtmaybe.core.diffparse import walk_diff

# ---------------------------------------------------------------------------
# Commentable-line index
# ---------------------------------------------------------------------------

# The set of (filename, line, side) tuples a review comment can attach to, where
# `line` is the new-file line for side "RIGHT" and the old-file line for "LEFT".
CommentableLines = set[tuple[str, int, str]]


def build_commentable_lines(diff: str) -> CommentableLines:
    """Parse a unified diff into the set of commentable (file, line, side) tuples.

    A review comment is anchored by file line and side, not by a running
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
    for path, kind, old_line, new_line, _text in walk_diff(diff):
        if kind != "+":  # deleted or context: present on the old side
            commentable.add((path, old_line, "LEFT"))
        if kind != "-":  # added or context: present on the new side
            commentable.add((path, new_line, "RIGHT"))
    return commentable


# ---------------------------------------------------------------------------
# Skip filter
# ---------------------------------------------------------------------------

# Lockfiles: skipped from review (nobody line-reviews a resolved dependency
# tree) but fetched for vulnerability scanning, which needs exactly these.
# Named separately so the two uses can never drift apart.
_LOCKFILES = frozenset(
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
        "pubspec.lock",
        "Podfile.lock",
        "packages.lock.json",
    }
)

# Human-written dependency declarations. Unlike lockfiles these stay reviewable
# — a version bump is a real change worth a comment — but they are also what a
# scanner reads when no lockfile changed.
_MANIFESTS = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "composer.json",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Pipfile",
    }
)

_SKIP_FILENAMES = _LOCKFILES | frozenset(
    {
        # Generated LLM-index corpora (llmstxt.org): machine-written whole-docs
        # dumps, never meaningfully line-reviewed.
        "llms.txt",
        "llms-full.txt",
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
    "__generated__/",
)

# Glob patterns matched against the full path.
_SKIP_GLOB_PATTERNS = (
    "*.min.js",
    "*.min.css",
    # Snapshot corpora: jest writes `.snap`, syrupy (Python) writes `.ambr`.
    "*.snap",
    "*.ambr",
    "*.pb.go",
    "*.pb.py",
    # protoc output for Python (`_pb2`) and C++ (`.pb.cc` / `.pb.h`). The `.proto`
    # source they are generated from stays reviewable.
    "*_pb2.py",
    "*_pb2.pyi",
    "*.pb.cc",
    "*.pb.h",
    # Dart/Flutter build_runner output: json_serializable/built_value (`.g.dart`),
    # freezed, and mockito's generated test doubles.
    "*.g.dart",
    "*.freezed.dart",
    "*.mocks.dart",
    "*.generated.*",
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
    pure = PurePosixPath(path)

    if pure.name in _SKIP_FILENAMES:
        return False

    # Extension check. A bare dotfile (".png") has no suffix — it is a config
    # file whose name starts with a dot, not an image.
    if pure.suffix.lower() in _SKIP_EXTENSIONS:
        return False

    # Directory prefix check (path must start with one of the blocked dirs)
    for prefix in _SKIP_DIR_PREFIXES:
        if path.startswith(prefix) or ("/" + prefix) in path:
            return False

    # Glob pattern check on the full path. Matching the path subsumes matching
    # the bare filename because fnmatch's "*" also matches "/" and every pattern
    # here is "*"-prefixed — a future pattern without one would need both arms.
    return not any(fnmatchcase(path, pattern) for pattern in _SKIP_GLOB_PATTERNS)


def is_scannable_manifest(path: str) -> bool:
    """Whether *path* is a dependency manifest or lockfile worth scanning.

    Deliberately independent of :func:`is_reviewable`: a lockfile is scannable
    but not reviewable, a manifest is both. Matches on the bare filename, so a
    nested ``frontend/yarn.lock`` counts — and ``requirements*.txt`` is matched
    by prefix, since projects split it (``requirements-dev.txt``).
    """
    name = PurePosixPath(path).name
    if name in _LOCKFILES or name in _MANIFESTS:
        return True
    return name.startswith("requirements") and name.endswith(".txt")
