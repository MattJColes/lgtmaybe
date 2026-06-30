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
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lgtmaybe.core.logging import get_logger

_log = get_logger(__name__)

# A shallow single-branch clone of one repo is quick; the timeout only caps a
# pathological clone, and a slow one degrades to "no corpus" rather than hanging.
_CLONE_TIMEOUT = 120

# Injected so tests don't shell out to real git. Mirrors subprocess.run's surface
# for the args we pass.
Runner = Callable[..., Any]


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
    filesystem or git failure — the caller treats that as "no corpus". The token is
    embedded in the remote URL only; ``capture_output`` keeps it out of surfaced
    stderr, and the clone lands in an ephemeral dir that is removed at exit.
    """
    try:
        dest = Path(tempfile.mkdtemp(prefix="lgtmaybe-base-"))
    except OSError:
        return None
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    try:
        runner(
            ["git", "clone", "--depth", "1", "--single-branch", "--branch", ref, url, str(dest)],
            capture_output=True,
            timeout=_CLONE_TIMEOUT,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        shutil.rmtree(dest, ignore_errors=True)
        return None
    atexit.register(shutil.rmtree, dest, ignore_errors=True)
    _log.info("cloned base tree for symbol resolution", extra={"repo": repo, "ref": ref})
    return dest
