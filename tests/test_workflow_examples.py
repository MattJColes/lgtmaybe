"""Structural guards for the supplied lgtmaybe workflows."""

import re
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
# The events lgtmaybe itself fires: posting a comment (auto-diagram, /ask, the
# summary) emits issue_comment, and posting inline findings emits
# pull_request_review_comment.
_SELF_TRIGGERED_EVENTS = {
    "issue_comment",
    "pull_request_review",
    "pull_request_review_comment",
}


def _workflows() -> list[Path]:
    return [_DOGFOOD_WORKFLOW, *sorted(_STARTER_WORKFLOWS.glob("*.yml"))]


def _triggers(workflow: dict) -> set[str]:
    # YAML 1.1 parses the `on:` key as the boolean True, so the trigger mapping
    # lands under True, not "on".
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, str):
        return {on}
    return set(on or ())


def _concurrency_blocks(workflow: dict) -> list[tuple[str, dict]]:
    """Every concurrency block in the file: workflow-level and each job's."""
    blocks = []
    if isinstance(workflow.get("concurrency"), dict):
        blocks.append(("workflow", workflow["concurrency"]))
    for name, job in (workflow.get("jobs") or {}).items():
        if isinstance(job.get("concurrency"), dict):
            blocks.append((f"jobs.{name}", job["concurrency"]))
    return blocks


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


def test_dogfood_workflow_reviews_with_the_image_it_builds() -> None:
    """The dogfood review must run this repo's code, not the last release.

    `uses: ./` only makes action.yml local; the container still defaults to the
    floating published tag, so reviews ran whatever was last released. That skew
    let a merged fix go unexercised for days — and let a review's own timeout
    contradict the action.yml sitting beside it. The job therefore builds the
    image from the (base) checkout and passes that tag to the action.
    """
    workflow = yaml.safe_load(_DOGFOOD_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["review"]["steps"]

    builds = [step for step in steps if "docker build" in str(step.get("run", ""))]
    assert builds, "the dogfood job must build the reviewer image from the checkout"
    build_command = str(builds[0]["run"])
    tag_match = re.search(r"--tag\s+(\S+)", build_command)
    assert tag_match, f"the build step must tag its image: {build_command!r}"

    [action_step] = [step for step in steps if step.get("uses") == "./"]
    assert action_step["with"]["image"] == tag_match.group(1), (
        "the dogfood review must run the locally built image, not the published tag"
    )


def test_starter_workflows_use_the_published_image() -> None:
    """The opposite guard for the examples users copy: they must NOT build from a
    checkout — they consume the released image via the published action ref."""
    for path in sorted(_STARTER_WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                assert "docker build" not in str(step.get("run", "")), (
                    f"{path.name} builds an image; starter workflows use the released one"
                )
                assert "image" not in (step.get("with") or {}), (
                    f"{path.name} overrides the container image; starters take the default"
                )


def test_dogfood_concurrency_only_applies_to_eligible_review_job() -> None:
    workflow = yaml.safe_load(_DOGFOOD_WORKFLOW.read_text(encoding="utf-8"))
    review_job = workflow["jobs"]["review"]

    assert "concurrency" not in workflow
    assert "if" in review_job
    assert review_job["concurrency"] == {
        "group": (
            "lgtmaybe-${{ github.event.pull_request.number || github.event.issue.number }}"
            "-${{ github.event_name }}"
        ),
        "cancel-in-progress": True,
    }


def test_self_triggered_workflows_discriminate_concurrency_by_event() -> None:
    # lgtmaybe posts comments during a review, and those comments fire the very
    # events these workflows subscribe to. A concurrency group keyed only on the
    # PR number puts the resulting run in the same group as the review, so
    # cancel-in-progress kills the review that posted the comment. The job-level
    # `if` guard cannot prevent this at workflow scope: the run joins the group
    # when it is created, before any job condition is evaluated. Keying the group
    # on the event name as well keeps a new push cancelling an in-flight review
    # while making lgtmaybe's own comments land in a different group.
    for path in _workflows():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        self_triggered = _triggers(workflow) & _SELF_TRIGGERED_EVENTS
        if not self_triggered:
            continue
        for scope, block in _concurrency_blocks(workflow):
            if not block.get("cancel-in-progress"):
                continue
            assert "github.event_name" in str(block.get("group", "")), (
                f"{path.name} subscribes to {sorted(self_triggered)} and cancels in progress, "
                f"but its {scope} concurrency group is not discriminated by event: "
                f"{block.get('group')!r} — lgtmaybe's own comments will cancel its reviews"
            )


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
