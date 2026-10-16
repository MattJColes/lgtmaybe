"""A merged stack layer hands its open children to its own base branch.

A stacked PR targets the branch of the PR below it. Squash-merging that lower
PR leaves its branch behind, and GitHub only retargets the PRs above it when
the branch is deleted. An upper PR merged after that lands on the dead branch,
never on main, so release-please never sees its conventional commit (#784 to
#797 were lost this way). `stack-retarget.yml` moves each open child onto the
merged PR's base the moment it merges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_WORKFLOW = Path(__file__).parents[1] / ".github/workflows/stack-retarget.yml"


def _load() -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return workflow


def _job() -> dict[str, Any]:
    jobs = _load()["jobs"]
    assert list(jobs) == ["retarget"]
    job: dict[str, Any] = jobs["retarget"]
    return job


def _run() -> str:
    return "\n".join(step.get("run", "") for step in _job()["steps"])


def test_it_fires_when_a_pull_request_closes() -> None:
    """Plain `pull_request`: a same-repository merge already gets a write
    token, so the riskier `pull_request_target` buys nothing here."""
    # PyYAML reads the bare `on:` key as True.
    assert _load()[True] == {"pull_request": {"types": ["closed"]}}


def test_it_acts_only_on_a_merge_from_this_repository() -> None:
    """A closed-unmerged PR leaves its children where they are, and a fork's
    head branch is not a branch here, so a same-named branch must not match."""
    condition = _job()["if"]

    assert "github.event.pull_request.merged" in condition
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition


def test_it_moves_children_of_the_merged_head_onto_the_merged_base() -> None:
    env = _job()["steps"][0]["env"]
    run = _run()

    assert env["HEAD_REF"] == "${{ github.event.pull_request.head.ref }}"
    assert env["BASE_REF"] == "${{ github.event.pull_request.base.ref }}"
    assert '-f state=open -f base="$HEAD_REF"' in run
    assert '-X PATCH "repos/$GITHUB_REPOSITORY/pulls/$pr" -f base="$BASE_REF"' in run


def test_it_never_checks_out_or_runs_pull_request_code() -> None:
    steps = _job()["steps"]

    assert all("uses" not in step for step in steps)
    assert "checkout" not in _WORKFLOW.read_text(encoding="utf-8")


def test_branch_names_reach_the_shell_through_env_only() -> None:
    """Branch names are author-chosen: interpolated into `run:` they would be
    shell code."""
    assert "${{" not in _run()


def test_the_token_can_edit_pull_requests_and_nothing_else() -> None:
    assert _load()["permissions"] == {}
    assert _job()["permissions"] == {"pull-requests": "write"}
