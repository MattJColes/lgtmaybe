"""The Azure Pipelines example users copy must keep secrets out of PR-editable YAML.

Azure Pipelines runs a pull request build from the YAML in the pull request's
merge result, so a step in the reviewed repository's own pipeline file can be
rewritten by anyone who can open a branch pull request. The example therefore
keeps every secret-bearing step in a template in a separate, protected
repository, and the reviewed repository's pipeline only ``extends`` it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "azure-devops"
PIPELINE = EXAMPLES / "azure-pipelines.yml"
TEMPLATE = EXAMPLES / "lgtmaybe-review.yml"


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_reviewed_repository_only_extends_a_template_from_another_repository() -> None:
    pipeline = _load(PIPELINE)

    template = pipeline["extends"]["template"]
    path, _, alias = template.partition("@")
    assert path and alias, "the template must be named in another repository: path@alias"

    repositories = {repo["repository"]: repo for repo in pipeline["resources"]["repositories"]}
    trusted = repositories[alias]
    assert trusted["type"] == "git"
    assert "/" in trusted["name"], "a separate project/repository, not this one"
    assert trusted["ref"].startswith("refs/heads/"), "pinned to a protected branch"


def test_the_reviewed_repository_carries_no_steps_or_secrets() -> None:
    """Anything here is PR-editable, so nothing secret-bearing may live here."""
    pipeline = _load(PIPELINE)
    text = PIPELINE.read_text(encoding="utf-8")

    for key in ("steps", "jobs", "stages", "variables"):
        assert key not in pipeline, f"{key} belongs in the protected template"
    assert "System.AccessToken" not in text
    assert "API_KEY" not in text
    assert "$(" not in text, "no variable or secret mapping at all"


def test_the_template_holds_the_review_job_and_reads_secrets_from_a_variable_group() -> None:
    template = _load(TEMPLATE)
    (job,) = template["jobs"]

    groups = [entry["group"] for entry in job["variables"] if "group" in entry]
    assert groups, "the model key comes from a variable group guarded by a Required template check"
    steps = job["steps"]
    assert steps[0] == {"checkout": "none"}, "PR code is never checked out"
    script = next(step for step in steps if "script" in step)
    assert "azure-pipelines" in script["script"]
    assert script["env"]["SYSTEM_ACCESSTOKEN"] == "$(System.AccessToken)"
