"""The Forgejo Actions workflows users copy must run out of the box and keep secrets safe.

Like Gitea, a Forgejo ``pull_request`` run takes its workflow file from the pull
request's own head commit, so the examples trigger on ``pull_request_target``
and never check out or run pull-request code. Forgejo runners have no default
labels, so the examples name the usual Docker-backed one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).parent.parent.parent
_EXAMPLES = sorted((_REPO_ROOT / "examples" / "forgejo").glob("*.yml"))
_MAJOR = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
].split(".", maxsplit=1)[0]


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[Any, Any]) -> set[str]:
    # YAML 1.1 parses the `on:` key as the boolean True.
    return set(workflow.get("on", workflow.get(True)) or ())


def _job(path: Path) -> dict[str, Any]:
    (job,) = _load(path)["jobs"].values()
    assert isinstance(job, dict)
    return job


def test_there_is_a_default_example() -> None:
    assert _REPO_ROOT / "examples" / "forgejo" / "lgtmaybe.yml" in _EXAMPLES


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.name)
class TestForgejoExample:
    def test_pull_requests_run_the_base_branch_workflow(self, path: Path) -> None:
        workflow = _load(path)

        assert _triggers(workflow) == {"pull_request_target", "issue_comment"}
        condition = _job(path)["if"]
        assert "github.event_name == 'pull_request_target'" in condition
        assert "'pull_request'" not in condition

    def test_the_job_never_checks_out_or_runs_pull_request_code(self, path: Path) -> None:
        job = _job(path)

        for step in job["steps"]:
            assert "uses" not in step, "no actions, so no checkout of the pull request head"
        assert job["steps"][-1]["run"] == "python -m lgtmaybe action"

    def test_runs_on_the_usual_docker_label_in_the_matching_image(self, path: Path) -> None:
        job = _job(path)

        assert job["runs-on"] == "docker"
        assert job["container"]["image"] == f"ghcr.io/mattjcoles/lgtmaybe:v{_MAJOR}"

    def test_its_own_comments_cannot_cancel_a_running_slash_command(self, path: Path) -> None:
        """A personal token's comments start workflow runs on Forgejo.

        Workflow-level concurrency applies before the job's `if`, so the run a
        posted comment starts would share the slash command's group and cancel
        it. A comment that does not start with `/` (all of lgtmaybe's) gets a
        group of its own, keyed on its run id; a real command still coalesces.
        """
        group = _load(path)["concurrency"]["group"]

        assert "github.event_name" in group
        assert "!startsWith(github.event.comment.body, '/')" in group
        assert "github.run_id" in group

    def test_the_token_comes_from_a_secret_name_forgejo_accepts(self, path: Path) -> None:
        """Forgejo refuses secret names starting FORGEJO_, GITEA_ or GITHUB_.

        `secrets.FORGEJO_TOKEN` is the automatic job token, which cannot read a
        commenter's permission, so the shipped example never uses it.
        """
        env = _job(path)["steps"][-1]["env"]

        secret = re.fullmatch(r"\$\{\{ secrets\.(\w+) \}\}", env["GITEA_TOKEN"])
        assert secret, "the token is read from a repository secret"
        assert secret.group(1).startswith("LGTMAYBE_")
