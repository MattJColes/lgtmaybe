"""Tests for RestGitHubGateway.create_check_run — the merge-gate check run.

The adapter POSTs a completed Check Run to the check-runs endpoint; branch
protection can then require it. Adapter-only, beyond the frozen port.
"""

from __future__ import annotations

import json

import httpx
import respx

from lgtmaybe.github import RestGitHubGateway

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"

BASE_URL = "https://api.github.com"
CHECK_RUNS_URL = f"{BASE_URL}/repos/{REPO}/check-runs"


@respx.mock
def test_create_check_run_posts_completed_run_with_conclusion() -> None:
    """create_check_run POSTs a completed run with the head sha, conclusion,
    and output, carrying the auth + API-version headers."""
    posted: list[dict[str, object]] = []
    seen_headers: list[httpx.Headers] = []

    def capture(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        seen_headers.append(request.headers)
        return httpx.Response(201, json={"id": 7})

    respx.route(method="POST", url=CHECK_RUNS_URL).mock(side_effect=capture)

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    gw.create_check_run(
        head_sha="deadbeef",
        conclusion="failure",
        title="1 finding at or above high",
        summary="Blocking findings present.",
    )

    assert len(posted) == 1
    body = posted[0]
    assert body["head_sha"] == "deadbeef"
    assert body["status"] == "completed"
    assert body["conclusion"] == "failure"
    assert body["output"] == {
        "title": "1 finding at or above high",
        "summary": "Blocking findings present.",
    }
    assert isinstance(body["name"], str) and body["name"]

    headers = seen_headers[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert headers["Accept"] == "application/vnd.github+json"


@respx.mock
def test_create_check_run_success_conclusion() -> None:
    """A success conclusion posts through the same endpoint."""
    posted: list[dict[str, object]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 8})

    respx.route(method="POST", url=CHECK_RUNS_URL).mock(side_effect=capture)

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    gw.create_check_run(
        head_sha="cafe1234",
        conclusion="success",
        title="No findings at or above high",
        summary="Clean.",
    )

    assert posted[0]["conclusion"] == "success"
    assert posted[0]["head_sha"] == "cafe1234"
