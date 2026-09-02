"""Tests for github/checkout.py — read-only base-branch clone for symbol resolution."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Any

import lgtmaybe.github.checkout as checkout
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
    assert cmd[0] == "git" and "clone" in cmd
    clone_at = cmd.index("clone")
    assert cmd[clone_at : clone_at + 4] == ["clone", "--depth", "1", "--single-branch"]
    assert "--branch" in cmd and cmd[cmd.index("--branch") + 1] == "main"
    # Plain (unauthenticated) base-repo URL, last positional is the destination.
    assert "https://github.com/owner/repo.git" in cmd
    assert cmd[-1] == str(dest)
    # Output captured (keeps the token out of surfaced stderr) and a timeout set.
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["timeout"] > 0


def test_clone_authenticates_via_environment_not_argv() -> None:
    captured: dict[str, Any] = {}

    def runner(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0)

    dest = clone_base_tree("owner/repo", "main", "tok-123", runner=runner)

    assert dest is not None
    cmd = captured["cmd"]
    assert all("tok-123" not in arg for arg in cmd)
    b64 = base64.b64encode(b"x-access-token:tok-123").decode()
    assert b64 not in " ".join(cmd)
    assert captured["env"]["GIT_CONFIG_COUNT"] == "1"
    assert captured["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert captured["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: basic {b64}"


def test_clone_failure_returns_none_and_cleans_up(tmp_path: Path) -> None:
    created: list[str] = []

    def runner(cmd: list[str], **kwargs: Any) -> Any:
        created.append(cmd[-1])  # the dest dir the helper made
        raise subprocess.CalledProcessError(128, cmd)

    dest = clone_base_tree("owner/repo", "main", "tok", runner=runner)

    assert dest is None
    # The temp dir the helper created was removed after the clone failed.
    assert created and not Path(created[0]).exists()


def test_clone_failure_stays_best_effort_when_cleanup_fails(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    dest = tmp_path / "clone"
    dest.mkdir()
    monkeypatch.setattr(checkout.tempfile, "mkdtemp", lambda **_kwargs: str(dest))

    def fail_cleanup(_path: Path) -> None:
        raise OSError("locked")

    monkeypatch.setattr(checkout, "_rmtree_force", fail_cleanup)

    def runner(cmd: list[str], **kwargs: Any) -> Any:
        raise subprocess.CalledProcessError(128, cmd)

    assert clone_base_tree("owner/repo", "main", "tok", runner=runner) is None


def test_clone_token_not_in_returned_path() -> None:
    def runner(cmd: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(cmd, 0)

    dest = clone_base_tree("owner/repo", "main", "supersecret", runner=runner)

    assert dest is not None
    assert "supersecret" not in str(dest)


def test_rmtree_force_removes_read_only_tree(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    child = tree / "child"
    child.mkdir(parents=True)
    (child / "file.txt").write_text("content", encoding="utf-8")
    child.chmod(0o500)

    checkout._rmtree_force(tree)

    assert not tree.exists()
