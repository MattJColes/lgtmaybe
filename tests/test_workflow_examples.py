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


def test_supplied_workflows_enable_auto_diagram() -> None:
    workflows = [_DOGFOOD_WORKFLOW, *_STARTER_WORKFLOWS.glob("*.yml")]

    for workflow in workflows:
        action_steps = [
            step
            for job in yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"].values()
            for step in job["steps"]
            if step.get("uses") in {_ACTION_REF, "./"}
        ]
        assert action_steps, f"{workflow} has no lgtmaybe Action step"
        assert all(step.get("with", {}).get("auto_diagram") is True for step in action_steps), (
            f"{workflow} must enable automatic diagrams for new repositories"
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
