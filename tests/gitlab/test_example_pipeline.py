"""The GitLab CI job users copy must keep its secrets to itself.

A merge request pipeline runs the CI configuration from the merge request's own
source branch, so where the file lives decides who can rewrite the job (the
guide covers that). Wherever it lives, the job's variables are scoped to an
environment only this job names, so the pipeline's other jobs (tests running the
merge request's code, third-party tools) never receive the model key or the
token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

EXAMPLE = Path(__file__).parent.parent.parent / "examples" / "gitlab" / ".gitlab-ci.yml"


def _load() -> dict[str, Any]:
    loaded = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_job_runs_only_on_merge_request_pipelines() -> None:
    job = _load()["lgtmaybe"]

    assert job["rules"] == [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
    assert "python -m lgtmaybe gitlab-ci" in job["script"][0]


def test_the_job_never_fetches_merge_request_code() -> None:
    job = _load()["lgtmaybe"]

    assert job["variables"]["GIT_STRATEGY"] == "none"


def test_the_secrets_are_scoped_to_an_environment_only_this_job_names() -> None:
    """Environment-scoped variables reach only jobs that name the environment."""
    pipeline = _load()
    job = pipeline["lgtmaybe"]

    assert job["environment"] == {"name": "lgtmaybe", "action": "access"}, (
        "`access` reads the environment's variables without recording a deployment"
    )
    others = [
        name
        for name, other in pipeline.items()
        if name != "lgtmaybe" and isinstance(other, dict) and "environment" in other
    ]
    assert not others, f"{others} would receive the review job's secrets"


def test_the_file_pulls_in_no_other_configuration() -> None:
    """An include can bring in jobs the merge request controls."""
    assert "include" not in _load()


def test_the_header_sends_the_reader_to_the_security_section() -> None:
    """Whoever copies the file must learn who can rewrite it before they add secrets."""
    text = EXAMPLE.read_text(encoding="utf-8")

    assert "Security: Who Can Change the Pipeline" in text
    assert "does not need to be Protected" not in text
