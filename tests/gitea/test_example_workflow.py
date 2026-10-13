"""The Gitea Actions workflow users copy must keep its secrets out of PR-editable YAML.

Gitea takes a `pull_request` workflow from the pull request's own head commit,
so a pull request from a branch in the repository could rewrite the job that
holds the model key and the token. `pull_request_target` (Gitea 1.21 and later)
takes the workflow from the base branch, as on GitHub. The job then only reads
the diff through the API, so it never checks out or runs pull-request code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

EXAMPLE = Path(__file__).parent.parent.parent / "examples" / "gitea" / "lgtmaybe.yml"


def _load() -> dict[str, Any]:
    loaded = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[Any, Any]) -> set[str]:
    # YAML 1.1 parses the `on:` key as the boolean True.
    return set(workflow.get("on", workflow.get(True)) or ())


def test_pull_requests_run_the_base_branch_workflow() -> None:
    workflow = _load()

    assert _triggers(workflow) == {"pull_request_target", "issue_comment"}
    condition = workflow["jobs"]["review"]["if"]
    assert "github.event_name == 'pull_request_target'" in condition
    assert "'pull_request'" not in condition


def test_the_job_never_checks_out_or_runs_pull_request_code() -> None:
    (job,) = _load()["jobs"].values()

    for step in job["steps"]:
        assert "uses" not in step, "no actions, so no checkout of the pull request head"
        assert step["run"] == "python -m lgtmaybe action"


def test_the_token_comes_from_a_secret_name_gitea_accepts() -> None:
    """Gitea refuses secret names starting GITEA_ or GITHUB_.

    `secrets.GITEA_TOKEN` is therefore always the automatic job token, never the
    scoped token the guide asks for.
    """
    (job,) = _load()["jobs"].values()
    env = job["steps"][0]["env"]

    secret = re.fullmatch(r"\$\{\{ secrets\.(\w+) \}\}", env["GITEA_TOKEN"])
    assert secret, "the token is read from a repository secret"
    assert not re.match(r"(?i)GIT(EA|HUB)_", secret.group(1))
