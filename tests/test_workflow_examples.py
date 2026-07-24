"""Structural guards for the supplied lgtmaybe workflows."""

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parent.parent
_DOGFOOD_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "lgtmaybe.yml"
_STARTER_WORKFLOWS = _REPO_ROOT / "examples" / "workflows"


def test_supplied_workflows_enable_auto_diagram() -> None:
    workflows = [_DOGFOOD_WORKFLOW, *_STARTER_WORKFLOWS.glob("*.yml")]

    for workflow in workflows:
        action_steps = [
            step
            for job in yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"].values()
            for step in job["steps"]
            if str(step.get("uses", "")).endswith("lgtmaybe@v0") or step.get("uses") == "./"
        ]
        assert action_steps, f"{workflow} has no lgtmaybe Action step"
        assert all(step.get("with", {}).get("auto_diagram") is True for step in action_steps), (
            f"{workflow} must enable automatic diagrams for new repositories"
        )
