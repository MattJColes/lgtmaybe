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


def test_default_base_falls_back_to_head_with_no_main_master_or_remote(tmp_path: Path) -> None:
    """A repo with no remote and no main/master branch resolves the base to HEAD
    (an empty comparison) rather than raising — the last link in the fallback
    chain origin/HEAD → origin/main → origin/master → main → master → HEAD."""
    from lgtmaybe.local import _default_base

    _git(tmp_path, "init", "-b", "trunk")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "base")

    assert _default_base(tmp_path) == "HEAD"


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


def test_uncommitted_resolves_head_with_a_single_rev_parse(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In --uncommitted mode the base and head are both HEAD; resolving it should
    cost one `git rev-parse HEAD` subprocess, not two."""
    import lgtmaybe.local as local_mod

    real_git = local_mod._git
    head_calls = 0

    def counting_git(cwd: Path | None, *args: str) -> str:
        nonlocal head_calls
        if args == ("rev-parse", "HEAD"):
            head_calls += 1
        return real_git(cwd, *args)

    monkeypatch.setattr(local_mod, "_git", counting_git)

    (repo / "app.py").write_text("def f():\n    return 99\n")
    ctx = local_pr_context(uncommitted=True, cwd=repo)

    assert head_calls == 1
    assert ctx.base_sha == ctx.head_sha


# ---------------------------------------------------------------------------
# untracked files — a brand-new file is the most common thing to review locally
# ---------------------------------------------------------------------------


def test_working_includes_a_brand_new_untracked_file(repo: Path) -> None:
    """`git diff` never shows untracked files, so a file you just wrote was
    invisible to --working — the exact case local review exists for."""
    (repo / "brand_new.py").write_text("def g():\n    return 7\n")

    ctx = local_pr_context(working=True, cwd=repo)

    assert "brand_new.py" in ctx.changed_files
    assert "+    return 7" in ctx.diff
    assert "+++ b/brand_new.py" in ctx.diff
    assert ctx.file_contents.get("brand_new.py") == "def g():\n    return 7\n"


def test_uncommitted_includes_a_brand_new_untracked_file(repo: Path) -> None:
    (repo / "brand_new.py").write_text("def g():\n    return 7\n")

    ctx = local_pr_context(uncommitted=True, cwd=repo)

    assert ctx.changed_files == ["brand_new.py"]
    assert "+    return 7" in ctx.diff


def test_untracked_file_in_a_subdirectory_is_repo_relative(repo: Path) -> None:
    """The synthesised patch header must carry the repo-relative path, or every
    finding on it anchors to a path GitHub/the CLI can't resolve."""
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("x = 1\n")

    ctx = local_pr_context(uncommitted=True, cwd=repo)

    assert ctx.changed_files == ["pkg/mod.py"]
    assert "+++ b/pkg/mod.py" in ctx.diff


def test_gitignored_file_is_not_reviewed(repo: Path) -> None:
    """Untracked is not the same as unwanted: honour .gitignore."""
    (repo / ".gitignore").write_text("secrets.env\n")
    (repo / "secrets.env").write_text("TOKEN=abc\n")

    ctx = local_pr_context(uncommitted=True, cwd=repo)

    assert "secrets.env" not in ctx.changed_files


def test_branch_mode_ignores_untracked_files(repo: Path) -> None:
    """Branch mode reviews committed history only — an untracked file is not
    part of it."""
    (repo / "brand_new.py").write_text("x = 1\n")

    ctx = local_pr_context(base="main", working=False, cwd=repo)

    assert "brand_new.py" not in ctx.changed_files


def test_untracked_and_tracked_edits_appear_together(repo: Path) -> None:
    (repo / "app.py").write_text("def f():\n    return 99\n")
    (repo / "brand_new.py").write_text("y = 2\n")

    ctx = local_pr_context(uncommitted=True, cwd=repo)

    assert set(ctx.changed_files) == {"app.py", "brand_new.py"}
    assert "+    return 99" in ctx.diff
    assert "+y = 2" in ctx.diff


# ---------------------------------------------------------------------------
# non-ASCII paths — git C-quotes them by default, which is not a real path
# ---------------------------------------------------------------------------


def test_non_ascii_path_is_not_c_quoted(repo: Path) -> None:
    """git's default `core.quotePath` renders `café.py` as `"caf\\303\\251.py"`
    — quotes, octal escapes and all. Left alone, that string is not a path any
    reader can open and not an extension `is_reviewable` recognises, so the file
    is silently dropped from the review."""
    (repo / "café.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "café.py")
    _git(repo, "commit", "-m", "feat: accented filename")

    ctx = local_pr_context(base="main", working=False, cwd=repo)

    assert ctx.changed_files == ["café.py"]
    assert "+++ b/café.py" in ctx.diff
    assert ctx.file_contents.get("café.py") == "value = 1\n"


def test_non_ascii_untracked_path_is_not_c_quoted(repo: Path) -> None:
    (repo / "naïve.py").write_text("value = 2\n", encoding="utf-8")

    ctx = local_pr_context(uncommitted=True, cwd=repo)

    assert ctx.changed_files == ["naïve.py"]
    assert "+++ b/naïve.py" in ctx.diff


def test_non_ascii_path_in_working_mode(repo: Path) -> None:
    (repo / "日本語.py").write_text("value = 3\n", encoding="utf-8")
    _git(repo, "add", "日本語.py")
    _git(repo, "commit", "-m", "feat: cjk filename")
    (repo / "日本語.py").write_text("value = 4\n", encoding="utf-8")

    ctx = local_pr_context(working=True, cwd=repo)

    assert ctx.changed_files == ["日本語.py"]
    assert "+value = 4" in ctx.diff
