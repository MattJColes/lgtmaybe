"""Structural guards for the supplied lgtmaybe workflows."""

import re
import tomllib
from pathlib import Path

import yaml

from lgtmaybe.cli.slash import SlashCommand

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


def _top_level_arms(condition: str) -> list[str]:
    """The `||`-separated arms of a job condition, split at paren depth zero.

    Substring and index checks over the whole condition cannot tell which arm a
    clause landed in — a guard hoisted into its own `||` branch reads exactly
    like one ANDed inside the arm it belongs to, and sits at the same offset.
    Splitting first is what lets an assertion name the arm it means.
    """
    arms: list[str] = []
    depth = start = index = 0
    while index < len(condition):
        if condition[index] == "(":
            depth += 1
        elif condition[index] == ")":
            depth -= 1
        elif depth == 0 and condition.startswith("||", index):
            arms.append(condition[start:index])
            index += 2
            start = index
            continue
        index += 1
    arms.append(condition[start:])
    return [arm.strip() for arm in arms]


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


def test_comment_arm_starts_no_job_without_a_slash_command() -> None:
    """A comment carrying no slash command must not start the issue_comment job.

    `issue_comment` fires on every comment on every PR. Without this guard the
    runner is claimed, the container pulled and Python booted, only for
    `execute_comment` to find no command and exit — correct, free of provider
    spend, and still a whole job. On a contended self-hosted pool that queueing
    delays the reviews that do have work to do.

    The guard is keyed on the command names the parser accepts, and is
    deliberately looser than `parse_command` (which additionally requires the
    command at the *start* of the body): a guard tighter than the parser would
    silently disable a command that used to work.
    """
    for path in _workflows():
        condition = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["review"]["if"]
        flat = " ".join(condition.split())
        arms = _top_level_arms(flat)

        for command in SlashCommand:
            assert f"github.event.comment.body, '/{command.value}'" in flat, (
                f"{path.name} does not admit /{command.value}; its `if:` guard is tighter "
                "than parse_command and would silently disable the command"
            )
        # `github.event.issue.pull_request` is what confines this arm to comments
        # on a pull request. Without it a `/review` on a plain issue starts a job,
        # and the arm also begins matching pull_request_review_comment events.
        comment_arms = [arm for arm in arms if "github.event.issue.pull_request" in arm]
        assert len(comment_arms) == 1, (
            f"{path.name} has no single issue_comment arm keyed on "
            "github.event.issue.pull_request — a comment on a plain issue would start a job"
        )
        # The command group must be ANDed onto the trusted-author check *inside*
        # that arm, with nothing but `&&` between them. As its own `||` branch —
        # or negated — it lets ANY commenter, a stranger included, start a job,
        # defeating the author-association gate whose whole purpose is to stop a
        # drive-by /review spending the provider budget. Matched on the
        # whitespace-normalised arm and blind to the order of the commands inside
        # the group, so a reflow or a reorder does not fail this.
        assert re.search(
            r"comment\.author_association\)\s*&&\s*\(\s*contains\(github\.event\.comment\.body",
            comment_arms[0],
        ), (
            f"{path.name} does not AND the slash-command group onto the trusted-author "
            "check inside the issue_comment arm — as a separate `||` branch (or "
            "negated) it lets any commenter start a job"
        )
        # The pull_request_review_comment arm is the answer_replies path, whose
        # replies are plain prose. Gating it on a command would disable the
        # feature outright, so it must inspect no comment body at all.
        [reply_arm] = [arm for arm in arms if "'pull_request_review_comment'" in arm]
        assert "github.event.comment.body" not in reply_arm, (
            f"{path.name} gates the reply arm on a slash command; replies carry none"
        )
        # One reference per command and nowhere else. With all five living in the
        # issue_comment arm checked above, this is what pins that no other arm —
        # pull_request_target included — gates on the comment body.
        assert flat.count("github.event.comment.body") == len(SlashCommand), (
            f"{path.name} references the comment body outside the issue_comment arm"
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


# The cap that was observed to STARVE the reviewer rather than bound it: at
# The floor USED to be 16,384, set from a real failure: at that cap a reasoning
# model spent the whole budget thinking and truncated 3 of 4 lenses on one PR,
# and 1 of 4 on a fifteen-line diff — where input size cannot be the cause.
#
# That floor is superseded, because the mechanism behind it is gone rather than
# because the failure was misread. `max_tokens` pays for thinking as well as
# output, and at the time `reasoning_effort` was being silently dropped on
# OpenRouter (#369), so thinking was unbounded and routinely ate 25,963-35,463
# tokens — which is what made a 16,384 cap starve an ordinary call. With the
# budget actually enforced, two measured runs on a 21-file diff put reasoning at
# 343-914 tokens on `low` and 961-3,624 on `medium`. Nothing can starve at 16k
# any more, and holding the floor there costs 4x the blast radius of every
# runaway for a danger that no longer exists.
#
# So the floor is re-derived from what a healthy call actually generates, with
# headroom, and it stays COUPLED to `reasoning_effort` — raise that and this
# must be re-measured, which is the trap the old floor was built out of.
_OBSERVED_HEALTHY_OUTPUT = 3_986
_HEADROOM = 2
_STARVED_CAP = _OBSERVED_HEALTHY_OUTPUT * _HEADROOM
# The ceiling deepseek-v4-pro ran to when a lens went away: 65,536 tokens and
# 21 minutes for a single call, against ~5.5k for the other three combined.
_RUNAWAY_OUTPUT = 65_536
# Effort settings the floor above has actually been measured against. A config
# outside this set has never been measured, and the floor is not evidence for it.
_MEASURED_EFFORTS = {"low", "medium"}


def test_dogfood_config_caps_what_one_call_may_generate() -> None:
    """A runaway generation is bounded, without starving an ordinary one.

    Truncation is legible (it names itself and its salvaged findings survive),
    but nothing stopped a lens burning a full output ceiling to get there. The cap
    is what makes that cost seconds instead of minutes — and on a prepaid route
    like OpenRouter it also shrinks the pre-flight reservation, which is charged
    against `max_tokens` before a single token is generated.

    The floor is the other half of the bargain: a cap below what a healthy lens
    generates reports a problem it created. It is derived from the largest output
    any healthy lens has been measured producing, times headroom — and it is only
    evidence for the `reasoning_effort` settings it was measured at, because this
    budget pays for thinking too.
    """
    config = yaml.safe_load((_REPO_ROOT / ".lgtmaybe.yml").read_text(encoding="utf-8"))
    cap = config.get("max_tokens")

    assert cap is not None, ".lgtmaybe.yml must cap max_tokens — see the reasoning above"
    assert config.get("reasoning_effort") in _MEASURED_EFFORTS, (
        f"reasoning_effort={config.get('reasoning_effort')!r} is outside the settings the "
        f"max_tokens floor was measured at ({sorted(_MEASURED_EFFORTS)}) — this budget pays "
        "for thinking as well as output, so re-measure the largest healthy output at the new "
        "setting before trusting the floor below"
    )
    assert cap >= _STARVED_CAP, (
        f"max_tokens={cap} leaves no headroom over the largest healthy lens output measured "
        f"({_OBSERVED_HEALTHY_OUTPUT} tokens) — a cap this low truncates ordinary calls "
        f"rather than only runaway ones. Floor is {_HEADROOM}x that, i.e. {_STARVED_CAP}"
    )
    assert cap < _RUNAWAY_OUTPUT, (
        f"max_tokens={cap} does not bound the runaway it exists to bound "
        f"({_RUNAWAY_OUTPUT} tokens, 21 minutes, one lens)"
    )
