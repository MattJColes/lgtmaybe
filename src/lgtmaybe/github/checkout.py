"""Read-only checkout of a PR's BASE tree for cross-file symbol resolution.

ast-grep needs a directory of source files to search. On the GitHub path there is
no working tree — the engine fetches the diff via API and never checks out PR
code. When the reflection auditor defers on a symbol it can't see, this module
shallow-clones the **base** branch (the trusted target repo, never the PR
head/fork) into a throwaway temp dir so ast-grep can locate that symbol's
definition. Cloning text and parsing it is not executing it, so this stays inside
the fork-safety model. Best-effort: any failure returns None and the resolver
simply finds nothing — a review never fails because a base clone didn't.
"""

from __future__ import annotations

import atexit
import base64
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lgtmaybe.core.logging import get_logger

_log = get_logger(__name__)

# A shallow single-branch clone of one repo is quick; the timeout only caps a
# pathological clone, and a slow one degrades to "no corpus" rather than hanging.
# Sized for a big monorepo on a cold runner — losing symbol resolution to an
# impatient clock costs the auditor real context.
_CLONE_TIMEOUT = 300

# Injected so tests don't shell out to real git. Mirrors subprocess.run's surface
# for the args we pass.
Runner = Callable[..., Any]


def _rmtree_force(path: Path) -> None:
    """Remove a tree, making Windows read-only entries writable when needed."""
    root = path.resolve()

    def retry(function: Callable[..., Any], failed_path: str, _error: Any) -> None:
        target = Path(failed_path)
        candidates = [target]
        try:
            target.parent.resolve().relative_to(root)
        except ValueError:
            pass
        else:
            candidates.append(target.parent)
        for candidate in candidates:
            try:
                candidate.chmod(candidate.stat().st_mode | stat.S_IWRITE | stat.S_IEXEC)
            except OSError:
                pass
        function(failed_path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _cleanup_base_tree(path: Path) -> None:
    try:
        _rmtree_force(path)
    except OSError as exc:
        _log.warning(
            "could not remove temporary base tree",
            extra={"path": str(path), "error": str(exc)},
        )


def clone_base_tree(
    repo: str,
    ref: str,
    token: str,
    *,
    runner: Runner = subprocess.run,
) -> Path | None:
    """Shallow-clone *repo* at branch *ref* into a temp dir; return it or None.

    *repo* is ``owner/name`` (the base repo). *ref* is the base branch name. The
    temp dir is registered for cleanup at process exit. Returns None on any
    filesystem or git failure — the caller treats that as "no corpus". The token
    is passed as a one-shot ``git -c http.<url>.extraheader`` basic-auth header
    (the actions/checkout approach) rather than embedded in the clone URL — a
    URL-embedded token is visible in the clear via /proc/*/cmdline and gets
    persisted as the remote URL in the temp clone's .git/config. The global
    ``-c`` applies to this invocation only (unlike ``git clone --config`` it is
    never written into the new clone's config); ``capture_output`` keeps it out
    of surfaced stderr, and the clone lands in an ephemeral dir removed at exit.
    """
    try:
        dest = Path(tempfile.mkdtemp(prefix="lgtmaybe-base-"))
    except OSError:
        return None
    url = f"https://github.com/{repo}.git"
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    auth_config = f"http.https://github.com/.extraheader=Authorization: basic {basic}"
    try:
        runner(
            [
                "git",
                "-c",
                auth_config,
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                ref,
                url,
                str(dest),
            ],
            capture_output=True,
            timeout=_CLONE_TIMEOUT,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        _cleanup_base_tree(dest)
        return None
    atexit.register(_cleanup_base_tree, dest)
    _log.info("cloned base tree for symbol resolution", extra={"repo": repo, "ref": ref})
    return dest
