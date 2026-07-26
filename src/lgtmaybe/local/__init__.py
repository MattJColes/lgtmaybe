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

from lgtmaybe.core.models import PRContext
from lgtmaybe.github.diff import is_reviewable, is_scannable_manifest

# `git diff` over a large working tree (or a repo whose objects are cold) can
# take a while; the timeout only caps a hung git, never a slow one.
_TIMEOUT = 120


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

    _ensure_repo(cwd)

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
    name_output = _git(cwd, "diff", "--name-only", spec)
    changed_files = [line for line in name_output.splitlines() if line]

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
    )


def local_file_reader(cwd: Path | None = None) -> Callable[[str], str | None]:
    """A read-only working-tree file reader for the engine's reflection pass.

    Returns the current text of a repo-relative ``path`` (the user's own branch —
    safe to read directly, no untrusted PR content), or None when the file is
    missing or unreadable. Lets the local CLI resolve a deferred reflection verdict
    the same way the GitHub gateway does, and finally gives local reviews grounding
    content. Paths that escape the repo root are refused.
    """
    root = Path(cwd) if cwd is not None else Path.cwd()
    root = root.resolve()

    def read(path: str) -> str | None:
        try:
            target = (root / path).resolve()
            target.relative_to(root)  # refuse paths that climb out of the repo
            return target.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    return read


def _git(cwd: Path | None, *args: str) -> str:
    """Run a git command and return stdout; raise ValueError on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
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


def _ensure_repo(cwd: Path | None) -> None:
    """Raise a clear ValueError unless cwd is inside a git work tree."""
    try:
        inside = _git(cwd, "rev-parse", "--is-inside-work-tree").strip()
    except ValueError as exc:
        raise ValueError("not a git repository (run lgtmaybe from inside one)") from exc
    if inside != "true":
        raise ValueError("not a git repository (run lgtmaybe from inside one)")


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
    toplevel = _git(cwd, "rev-parse", "--show-toplevel").strip()
    return Path(toplevel).name
