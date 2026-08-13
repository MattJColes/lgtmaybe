"""The GraphQL thread-reply primitive used by resolve-on-fix."""

from __future__ import annotations

import json

import httpx
import respx

from lgtmaybe.github import RestGitHubGateway

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"

BASE_URL = "https://api.github.com"
GRAPHQL_URL = f"{BASE_URL}/graphql"


def _gateway() -> RestGitHubGateway:
    return RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN)


@respx.mock
def test_resolve_reply_posts_mutation_to_the_thread() -> None:
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"data": {"addPullRequestReviewThreadReply": {}}})

    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=capture)

    _gateway().reply_in_thread("THREAD_OURS", "✅ Looks resolved.")

    assert "addPullRequestReviewThreadReply" in captured["query"]
    variables = captured["variables"]
    assert variables["threadId"] == "THREAD_OURS"
    assert variables["body"] == "✅ Looks resolved."
