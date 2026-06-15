"""local_pr_context builds a PRContext from real git, no GitHub involved."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lgtmaybe.local import local_pr_context


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo on `main` with one base commit, plus a `feature` branch."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    # Never sign commits in a throwaway test repo — inheriting a global
    # commit.gpgsign would otherwise fail the fixture in environments that
    # force signing (e.g. a signing server that rejects scratch repos).
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "base")
    _git(tmp_path, "checkout", "-b", "feature")
    return tmp_path


def test_branch_vs_base_captures_committed_change(repo: Path) -> None:
    (repo / "app.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-am", "change")

    ctx = local_pr_context(base="main", working=False, cwd=repo)

    assert "+    return 2" in ctx.diff
    assert ctx.changed_files == ["app.py"]
    assert ctx.head_sha and ctx.base_sha
    assert ctx.pr_number == 0


def test_working_captures_uncommitted_change(repo: Path) -> None:
    (repo / "app.py").write_text("def f():\n    return 99\n")  # not committed

    ctx = local_pr_context(working=True, cwd=repo)

    assert "+    return 99" in ctx.diff
    assert ctx.changed_files == ["app.py"]


def test_branch_vs_base_ignores_uncommitted_when_not_working(repo: Path) -> None:
    (repo / "app.py").write_text("def f():\n    return 99\n")  # working tree only

    ctx = local_pr_context(base="main", working=False, cwd=repo)

    assert ctx.diff == ""
    assert ctx.changed_files == []


def test_repo_name_from_remote(repo: Path) -> None:
    _git(repo, "remote", "add", "origin", "git@github.com:owner/myrepo.git")
    (repo / "app.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-am", "change")

    ctx = local_pr_context(base="main", working=False, cwd=repo)

    assert ctx.repo == "owner/myrepo"


def test_not_a_git_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="git"):
        local_pr_context(base="main", working=False, cwd=tmp_path)


def test_branch_mode_collects_commit_subjects(repo: Path) -> None:
    """Commit names are the CLI's stated intent — the local counterpart to a PR
    title — so the intent lens works without GitHub."""
    (repo / "app.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-am", "feat: return two")
    (repo / "app.py").write_text("def f():\n    return 3\n")
    _git(repo, "commit", "-am", "fix: actually return three")

    ctx = local_pr_context(base="main", working=False, cwd=repo)

    # Newest first, branch commits only — the base commit is not intent.
    assert ctx.commit_messages == ["fix: actually return three", "feat: return two"]
    assert ctx.title == ""  # no PR title locally


def test_working_mode_collects_commit_subjects(repo: Path) -> None:
    """Working mode compares the whole worktree to the base, so the branch's
    commit names are still the stated intent."""
    (repo / "app.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-am", "feat: return two")
    (repo / "app.py").write_text("def f():\n    return 99\n")  # uncommitted on top

    ctx = local_pr_context(working=True, cwd=repo)

    assert ctx.commit_messages == ["feat: return two"]


def test_working_mode_on_base_tip_has_no_commit_subjects(repo: Path) -> None:
    """No commits beyond the base → nothing states an intent; the lens is skipped."""
    (repo / "app.py").write_text("def f():\n    return 99\n")

    ctx = local_pr_context(working=True, cwd=repo)

    assert ctx.commit_messages == []


# ---------------------------------------------------------------------------
# Comparing to the remote primary branch
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_remote(tmp_path: Path) -> Path:
    """A clone whose origin/main has advanced past the stale local main.

    origin/HEAD is deliberately unset (as after `git remote add` or cloning an
    empty repo) to exercise the fallback, and the local main is one commit
    behind origin/main — resolving the base to the LOCAL main would be wrong.
    """
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "app.py").write_text("def f():\n    return 1\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "base")
    _git(repo, "push", "-u", "origin", "main")
    # origin/main advances; local main resets back to the stale commit.
    (repo / "other.py").write_text("x = 1\n")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-m", "remote advance")
    _git(repo, "push", "origin", "main")
    _git(repo, "reset", "--hard", "HEAD~1")
    _git(repo, "checkout", "-b", "feature")
    return repo


def test_default_base_prefers_remote_main_over_stale_local_main(
    repo_with_remote: Path,
) -> None:
    """With origin/HEAD unset, the default base must still be the REMOTE primary
    branch — not a stale local main that happens to share its name."""
    repo = repo_with_remote
    (repo / "app.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-am", "feat: change")

    ctx = local_pr_context(working=False, cwd=repo)

    assert ctx.base_sha == _git(repo, "rev-parse", "origin/main")
    assert ctx.base_sha != _git(repo, "rev-parse", "main")
    assert "+    return 2" in ctx.diff


def test_working_mode_compares_worktree_to_remote_main(repo_with_remote: Path) -> None:
    """Working mode reviews the whole worktree against the remote primary branch:
    branch commits AND uncommitted edits, based at the merge-base so commits that
    only exist on origin/main don't show up as reversed changes."""
    repo = repo_with_remote
    (repo / "app.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-am", "feat: committed change")
    (repo / "app.py").write_text("def f():\n    return 2\nEXTRA = True\n")  # uncommitted

    ctx = local_pr_context(working=True, cwd=repo)

    assert "+    return 2" in ctx.diff  # the branch commit is included
    assert "+EXTRA = True" in ctx.diff  # so is the uncommitted edit
    # Based at merge-base(origin/main, HEAD): origin-only commits aren't reversed.
    assert ctx.base_sha == _git(repo, "merge-base", "origin/main", "HEAD")
    assert "other.py" not in ctx.changed_files
    assert ctx.commit_messages == ["feat: committed change"]


def test_working_mode_honours_base_override(repo: Path) -> None:
    """--base still wins in working mode."""
    (repo / "app.py").write_text("def f():\n    return 99\n")

    ctx = local_pr_context(base="main", working=True, cwd=repo)

    assert "+    return 99" in ctx.diff
    assert ctx.base_sha == _git(repo, "rev-parse", "main")


# ---------------------------------------------------------------------------
# --uncommitted: only the working-tree edits, vs HEAD
# ---------------------------------------------------------------------------


def test_uncommitted_reviews_only_uncommitted_changes(repo: Path) -> None:
    """--uncommitted is the narrow view: working-tree edits vs HEAD, with the
    branch's committed changes excluded."""
    (repo / "app.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-am", "feat: return two")
    (repo / "app.py").write_text("def f():\n    return 99\n")  # uncommitted on top

    ctx = local_pr_context(uncommitted=True, cwd=repo)

    assert "+    return 99" in ctx.diff
    assert "+    return 2" not in ctx.diff  # the committed change is excluded
    assert ctx.base_sha == _git(repo, "rev-parse", "HEAD")
    # Uncommitted edits aren't described by any commit message — no stated intent.
    assert ctx.commit_messages == []


def test_working_and_uncommitted_are_mutually_exclusive(repo: Path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        local_pr_context(working=True, uncommitted=True, cwd=repo)
