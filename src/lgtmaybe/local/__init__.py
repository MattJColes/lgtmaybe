"""Build a PRContext from the local git repo — the engine input for local mode.

This is the local counterpart to the GitHub REST gateway: instead of fetching a
PR's diff over the API, it shells out to ``git`` so a human can review their
current branch (or working tree) with no GitHub involvement at all.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from lgtmaybe.core.diff import is_reviewable, is_scannable_manifest
from lgtmaybe.core.diffparse import split_by_file
from lgtmaybe.core.models import PRContext

# `git diff` over a large working tree (or a repo whose objects are cold) can
# take a while; the timeout only caps a hung git, never a slow one.
_TIMEOUT = 120

# git's default `core.quotePath` renders a non-ASCII path as a C-quoted string:
# `café.py` comes back as `"caf\303\251.py"`, quotes and octal escapes included.
# That is not a path anything can open, its apparent extension is `py"`, and the
# patch header stops matching `b/<path>` — so an accented or CJK filename was
# silently dropped from the review. We already decode git's output as UTF-8, so
# turning the quoting off gives us the real bytes.
_QUOTE_PATH_OFF = ("-c", "core.quotePath=false")


def local_pr_context(
    *,
    base: str | None = None,
    working: bool = False,
    uncommitted: bool = False,
    cwd: Path | None = None,
) -> PRContext:
    """Return a PRContext for the local repo, compared against the remote primary branch.

    Branch and ``working`` mode resolve the same base — the remote's default
    branch (``origin/HEAD``, else the first of ``origin/main``/``origin/master``/
    ``main``/``master`` that exists), overridable with ``base``:

    - default: the branch's committed changes (``git diff <base>...HEAD``).
    - ``working``: the whole worktree — branch commits **plus** uncommitted
      edits — diffed against the merge-base with ``base``, so commits that only
      exist on the remote don't show up as reversed changes.
    - ``uncommitted``: the narrow view — only working-tree edits, vs HEAD
      (no base involved). Mutually exclusive with ``working``.

    Commit subjects between the base and HEAD are collected in branch and
    working mode (the stated intent for the intent lens); uncommitted edits are
    not described by any commit, so ``uncommitted`` mode collects none. Raises
    ValueError when git is missing or this is not a git repository.
    """
    if working and uncommitted:
        raise ValueError("--working and --uncommitted are mutually exclusive")

    cwd = _ensure_repo(cwd)
    # Everything downstream — the patch paths and the reader that
    # opens each changed file — speaks repo-relative paths, because that is what
    # `git diff` reports wherever it is invoked from. Anchor on the repo root so
    # a review run from a subdirectory sees the same worktree as one run from
    # the top, rather than half of it against the wrong base directory.
    head_sha = _git(cwd, "rev-parse", "HEAD").strip()

    if uncommitted:
        spec = "HEAD"
        base_sha = head_sha  # base and head are both HEAD — resolved once above
        commit_messages: list[str] = []
    else:
        base_ref = base or _default_base(cwd)
        if working:
            merge_base = _git(cwd, "merge-base", base_ref, "HEAD").strip()
            spec = merge_base
            base_sha = merge_base
        else:
            spec = f"{base_ref}...HEAD"
            base_sha = _git(cwd, "rev-parse", base_ref).strip()
        # Commit names are the local stated intent — the CLI counterpart to a PR
        # title — feeding the intent lens. Empty when HEAD sits on the base.
        commit_messages = _commit_subjects(cwd, base_ref)

    diff = _git(cwd, "diff", spec)
    changed_files = [path for path, _patch in split_by_file(diff, []) if path != "unknown"]

    # `git diff` only ever reports content git already tracks, so a file you
    # just created is invisible to it — and "review my working tree" almost
    # always means "review the file I just wrote". Branch mode reviews committed
    # history, where an untracked file genuinely has no place.
    if working or uncommitted:
        untracked = _untracked_files(cwd)
        if untracked:
            diff += _untracked_patches(cwd, untracked)
            changed_files += untracked

    # Working-tree text for the changed files. Static analysis has never run on
    # the local CLI because nothing populated these; the reader is the same
    # traversal-guarded one reflection already uses.
    read = local_file_reader(cwd)
    file_contents: dict[str, str] = {}
    scan_contents: dict[str, str] = {}
    for path in changed_files:
        if is_reviewable(path):
            text = read(path)
            if text is not None:
                file_contents[path] = text
        elif is_scannable_manifest(path):
            text = read(path)
            if text is not None:
                scan_contents[path] = text

    return PRContext(
        diff=diff,
        changed_files=changed_files,
        file_contents=file_contents,
        scan_contents=scan_contents,
        base_sha=base_sha,
        head_sha=head_sha,
        repo=_repo_name(cwd),
        pr_number=0,
        commit_messages=commit_messages,
        head_branch=_current_branch(cwd),
    )


def local_file_reader(cwd: Path | None = None) -> Callable[[str], str | None]:
    """A read-only working-tree file reader for the engine's reflection pass.

    Returns the current text of a repo-relative ``path`` (the user's own branch —
    safe to read directly, no untrusted PR content), or None when the file is
    missing or unreadable. Lets the local CLI resolve a deferred reflection verdict
    the same way the GitHub gateway does, and finally gives local reviews grounding
    content. Paths that escape the repo root are refused.

    ``cwd`` is the root to resolve against, used verbatim — the eval harness
    points it at a fixture corpus that is not a repo of its own. Omitting it
    means "this repo", which resolves the worktree's top level rather than the
    process's directory: the paths handed to the reader are repo-relative,
    because that is what git reports wherever it runs.
    """
    root = (Path(cwd) if cwd is not None else local_repo_root()).resolve()

    def read(path: str) -> str | None:
        try:
            target = (root / path).resolve()
            target.relative_to(root)  # refuse paths that climb out of the repo
            return target.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    return read


def local_repo_root(cwd: Path | None = None) -> Path:
    """The worktree's top level, or *cwd* itself when git can't say.

    Falling back rather than raising keeps a non-repo caller (the file reader's
    default) behaving exactly as it did before, instead of turning a "no such
    file" into a crash.
    """
    try:
        return Path(_git(cwd, "rev-parse", "--show-toplevel").strip())
    except ValueError:
        return Path(cwd) if cwd is not None else Path.cwd()


def _untracked_files(cwd: Path | None) -> list[str]:
    """Repo-relative paths of untracked files worth reviewing, .gitignore honoured.

    ``--exclude-standard`` applies the same ignore rules git itself uses, so a
    build directory or a local ``.env`` never shows up. The reviewability filter
    is applied here rather than downstream so a repo full of un-ignored
    junk (images, archives) costs no ``git diff`` subprocesses at all.
    """
    listing = _git(cwd, "ls-files", "--others", "--exclude-standard", "-z")
    return [
        path
        for path in listing.split("\0")
        if path and (is_reviewable(path) or is_scannable_manifest(path))
    ]


def _untracked_patches(cwd: Path | None, paths: list[str]) -> str:
    """New-file patches for untracked *paths*, in the shape `git diff` would emit.

    ``git diff --no-index`` renders each file as the add-everything patch git
    would have produced had it been staged, without touching the user's index.
    It exits 1 when the two inputs differ — the normal case here — so the exit
    code is read as "there is a diff", not as failure. A file that vanishes
    between the listing and the diff is skipped rather than failing the review.
    """
    patches: list[str] = []
    for path in paths:
        try:
            result = subprocess.run(
                ["git", *_QUOTE_PATH_OFF, "diff", "--no-index", "--", "/dev/null", path],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_TIMEOUT,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        # 0 = identical (an empty file), 1 = differs. Anything else is a real
        # git failure on this one path; skip it rather than lose the review.
        if result.returncode in (0, 1) and result.stdout:
            patches.append(result.stdout)
    return "".join(patches)


def _git(cwd: Path | None, *args: str) -> str:
    """Run a git command and return stdout; raise ValueError on failure."""
    try:
        result = subprocess.run(
            ["git", *_QUOTE_PATH_OFF, *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise ValueError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return result.stdout


def _ensure_repo(cwd: Path | None) -> Path:
    """Return the worktree root, or raise a clear ValueError outside one."""
    try:
        return Path(_git(cwd, "rev-parse", "--show-toplevel").strip())
    except ValueError as exc:
        raise ValueError("not a git repository (run lgtmaybe from inside one)") from exc


def _current_branch(cwd: Path | None) -> str:
    """The checked-out branch name, or "" on a detached HEAD or any git failure.

    Feeds the spec lens's branch signal: spec-driven workflows name the branch
    after the spec directory, so locally this is often the only thing tying a
    review to the spec it delivers.
    """
    try:
        branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except ValueError:
        return ""
    return "" if branch == "HEAD" else branch


def _commit_subjects(cwd: Path | None, base_ref: str) -> list[str]:
    """Subject lines of the branch's commits (newest first), excluding *base_ref*."""
    log = _git(cwd, "log", "--format=%s", f"{base_ref}..HEAD")
    return [line for line in log.splitlines() if line.strip()]


def _default_base(cwd: Path | None) -> str:
    """The remote primary branch, falling back through local names.

    ``origin/HEAD`` is only set by a normal clone of a non-empty repo; after
    ``git remote add`` (or cloning an empty repo) it is missing, and a bare
    ``main`` fallback would silently compare against a possibly stale LOCAL
    main. So prefer the remote-tracking refs before any local branch, and end
    at HEAD (an empty comparison) rather than failing.
    """
    try:
        return _git(cwd, "rev-parse", "--abbrev-ref", "origin/HEAD").strip()
    except ValueError:
        pass
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _ref_exists(cwd, candidate):
            return candidate
    return "HEAD"


def _ref_exists(cwd: Path | None, ref: str) -> bool:
    try:
        _git(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except ValueError:
        return False
    return True


def _repo_name(cwd: Path | None) -> str:
    """'owner/repo' from the origin remote, else the work-tree directory name."""
    try:
        url = _git(cwd, "remote", "get-url", "origin").strip()
    except ValueError:
        url = ""
    if url:
        parts = re.split(r"[:/]", url.removesuffix(".git"))
        return "/".join(parts[-2:])
    return local_repo_root(cwd).name
