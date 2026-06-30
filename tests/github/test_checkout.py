"""Tests for github/checkout.py — read-only base-branch clone for symbol resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from lgtmaybe.github.checkout import clone_base_tree


def test_clone_builds_shallow_single_branch_command() -> None:
    captured: dict[str, Any] = {}

    def runner(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0)

    dest = clone_base_tree("owner/repo", "main", "tok-123", runner=runner)

    assert dest is not None and dest.exists()
    cmd = captured["cmd"]
    assert cmd[:5] == ["git", "clone", "--depth", "1", "--single-branch"]
    assert "--branch" in cmd and cmd[cmd.index("--branch") + 1] == "main"
    # Authenticated base-repo URL, last positional is the destination.
    assert "https://x-access-token:tok-123@github.com/owner/repo.git" in cmd
    assert cmd[-1] == str(dest)
    # Output captured (keeps the token out of surfaced stderr) and a timeout set.
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["timeout"] > 0


def test_clone_failure_returns_none_and_cleans_up(tmp_path: Path) -> None:
    created: list[str] = []

    def runner(cmd: list[str], **kwargs: Any) -> Any:
        created.append(cmd[-1])  # the dest dir the helper made
        raise subprocess.CalledProcessError(128, cmd)

    dest = clone_base_tree("owner/repo", "main", "tok", runner=runner)

    assert dest is None
    # The temp dir the helper created was removed after the clone failed.
    assert created and not Path(created[0]).exists()


def test_clone_token_not_in_returned_path() -> None:
    def runner(cmd: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(cmd, 0)

    dest = clone_base_tree("owner/repo", "main", "supersecret", runner=runner)

    assert dest is not None
    assert "supersecret" not in str(dest)
