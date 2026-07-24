"""Gateway primitives for conversational finding threads.

Two adapter-only methods, both GraphQL (respx-mocked):

- ``find_review_thread(comment_id)`` resolves the REST review-comment id of an
  inbound reply to its thread's GraphQL node id and the thread's root-comment
  body — so the caller can recognise a thread lgtmaybe opened and reply into it;
- ``reply_in_thread(thread_id, body)`` posts a reply on that thread via the
  ``addPullRequestReviewThreadReply`` mutation.
"""

from __future__ import annotations

import json

import httpx
import respx

from lgtmaybe.github import RestGitHubGateway
from lgtmaybe.github.rest_gateway import finding_fingerprint

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"

BASE_URL = "https://api.github.com"
GRAPHQL_URL = f"{BASE_URL}/graphql"

FP = finding_fingerprint("src/app.py", "possible NPE")
ROOT_BODY = f"**[HIGH] possible NPE**\n\n`user` may be None.\n\n<!-- lgtmaybe-finding:{FP} -->"


def _gateway() -> RestGitHubGateway:
    return RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN)


def _threads_payload(nodes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def _thread_node(tid: str, comment_ids: list[int], root_body: str) -> dict[str, object]:
    nodes = [
        {"databaseId": cid, "body": root_body if i == 0 else "reply"}
        for i, cid in enumerate(comment_ids)
    ]
    return {"id": tid, "comments": {"nodes": nodes}}


# ---------------------------------------------------------------------------
# find_review_thread: comment id -> (thread node id, root comment body)
# ---------------------------------------------------------------------------


@respx.mock
def test_find_review_thread_matches_by_comment_database_id() -> None:
    nodes = [
        _thread_node("THREAD_OTHER", [111], "someone else's thread"),
        _thread_node("THREAD_OURS", [555, 556], ROOT_BODY),
    ]
    respx.route(method="POST", url=GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json=_threads_payload(nodes))
    )

    # A reply whose in_reply_to_id is 555 (or the mid-thread 556) resolves to
    # the thread carrying it, and returns its root comment's body.
    assert _gateway().find_review_thread(556) == ("THREAD_OURS", ROOT_BODY)


@respx.mock
def test_find_review_thread_none_when_no_thread_matches() -> None:
    nodes = [_thread_node("THREAD_OTHER", [111], "x")]
    respx.route(method="POST", url=GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json=_threads_payload(nodes))
    )

    assert _gateway().find_review_thread(999) is None


# ---------------------------------------------------------------------------
# reply_in_thread: posts addPullRequestReviewThreadReply to the right thread
# ---------------------------------------------------------------------------


@respx.mock
def test_reply_in_thread_posts_reply_mutation_to_the_thread() -> None:
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"data": {"addPullRequestReviewThreadReply": {}}})

    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=capture)

    _gateway().reply_in_thread("THREAD_OURS", "Good point — you're right.")

    assert "addPullRequestReviewThreadReply" in captured["query"]
    variables = captured["variables"]
    assert variables["threadId"] == "THREAD_OURS"
    assert variables["body"] == "Good point — you're right."
