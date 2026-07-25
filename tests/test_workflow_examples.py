"""Structural guards for the supplied lgtmaybe workflows."""

import tomllib
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parent.parent
_DOGFOOD_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "lgtmaybe.yml"
_PROJECT_VERSION = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]
_ACTION_REF = f"MattJColes/lgtmaybe@v{_PROJECT_VERSION.split('.', maxsplit=1)[0]}"
_STARTER_WORKFLOWS = _REPO_ROOT / "examples" / "workflows"


def test_supplied_workflows_rely_on_the_auto_diagram_default() -> None:
    # auto_diagram defaults to on in ReviewConfig, so a workflow that sets it
    # explicitly is redundant config that would mask a default regression.
    workflows = [_DOGFOOD_WORKFLOW, *_STARTER_WORKFLOWS.glob("*.yml")]

    for workflow in workflows:
        action_steps = [
            step
            for job in yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"].values()
            for step in job["steps"]
            if step.get("uses") in {_ACTION_REF, "./"}
        ]
        assert action_steps, f"{workflow} has no lgtmaybe Action step"
        assert all("auto_diagram" not in step.get("with", {}) for step in action_steps), (
            f"{workflow} sets auto_diagram explicitly; the default already enables it"
        )


def test_dogfood_workflow_prints_the_timing_profile() -> None:
    workflow = yaml.safe_load(_DOGFOOD_WORKFLOW.read_text(encoding="utf-8"))
    [action_step] = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses") == "./"
    ]
    assert action_step.get("with", {}).get("profile") is True


def test_dogfood_concurrency_only_applies_to_eligible_review_job() -> None:
    workflow = yaml.safe_load(_DOGFOOD_WORKFLOW.read_text(encoding="utf-8"))
    review_job = workflow["jobs"]["review"]

    assert "concurrency" not in workflow
    assert "if" in review_job
    assert review_job["concurrency"] == {
        "group": "lgtmaybe-${{ github.event.pull_request.number || github.event.issue.number }}",
        "cancel-in-progress": True,
    }


def test_dogfood_workflow_uses_the_public_app_identity() -> None:
    workflow = yaml.safe_load(_DOGFOOD_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"]["id-token"] == "write"
    [action_step] = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses") == "./"
    ]
    inputs = action_step["with"]
    assert inputs["github_identity"] == "lgtmaybe"
    assert "app_id" not in inputs
    assert "app_private_key" not in inputs
