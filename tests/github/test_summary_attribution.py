"""Summary completion and updates belong only to the authenticated posting actor."""

from __future__ import annotations

import json

import httpx
import pytest

from lgtmaybe.cli import run_review
from lgtmaybe.core.models import PRContext, ProviderResult, ReviewConfig
from lgtmaybe.engine.engine import LLMReviewEngine
from lgtmaybe.github import RestGitHubGateway
from tests.fakes import FakeProvider

OLD = "a" * 40
HEAD = "b" * 40
MARKER = "<!-- lgtmaybe:openrouter/test -->"


def _review(review_id: int, head: str, actor: str) -> dict:
    return {
        "id": review_id,
        "body": f"{MARKER}\n<!-- lgtmaybe-reviewed:{head} -->",
        "commit_id": head,
        "user": {"login": actor},
    }


@pytest.mark.parametrize(
    "actor", ["github-actions[bot]", "pat-user", "custom-review[bot]", "lgtmaybe[bot]"]
)
def test_latest_summary_ignores_copied_markers_from_another_actor(actor: str) -> None:
    reviews = [_review(99, OLD, actor), _review(100, HEAD, "untrusted-pr-reviewer")]
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST" and request.url.path == "/graphql":
            assert json.loads(request.content)["query"] == "query { viewer { login } }"
            return httpx.Response(200, json={"data": {"viewer": {"login": actor}}})
        assert request.method == "GET" and request.url.path.endswith("/reviews")
        return httpx.Response(200, json=reviews)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = RestGitHubGateway(
            "owner/repo", 42, "synthetic", client=client, marker_key="openrouter/test"
        )
        assert gateway.last_completed_sha(diagram_required=False) == OLD
        assert gateway.last_completed_sha(diagram_required=False) == OLD
    assert sum(r.method == "POST" for r in calls) == 1


@pytest.mark.parametrize("trusted_head", [OLD, HEAD])
def test_real_engine_reviews_forged_completion_but_reuses_trusted_current_head(
    trusted_head: str,
) -> None:
    reviews = [
        _review(99, trusted_head, "github-actions[bot]"),
        _review(100, HEAD, "untrusted-pr-reviewer"),
    ]
    posted = []

    def handle(request):
        if request.method == "POST" and request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"viewer": {"login": "github-actions[bot]"}}})
        if request.method == "GET" and request.url.path.endswith("/reviews"):
            return httpx.Response(200, json=reviews)
        if request.method == "GET" and "/compare/" in request.url.path:
            return httpx.Response(200, json={"status": "identical"})
        if request.method == "POST" and request.url.path.endswith("/reviews"):
            posted.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 101})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = FakeProvider(
        result=ProviderResult(text='{"findings":[]}', input_tokens=1, output_tokens=1)
    )
    ctx = PRContext(
        diff="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        changed_files=["a.py"],
        base_sha=OLD,
        head_sha=HEAD,
        repo="owner/repo",
        pr_number=42,
    )
    cfg = ReviewConfig(
        provider="openrouter",
        model="test",
        incremental=True,
        reflect=False,
        risk={"enabled": False},
        learn_feedback=False,
        auto_diagram=False,
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = RestGitHubGateway(
            "owner/repo",
            42,
            "synthetic",
            client=client,
            marker_key="openrouter/test",
            resolve_fixed=False,
        )
        findings, summary = run_review(
            github=gateway, engine=LLMReviewEngine(provider), cfg=cfg, ctx=ctx, dry_run=False
        )
    assert findings == []
    if trusted_head == HEAD:
        assert not provider.calls and not posted
        assert "already complete" in summary
    else:
        assert len(provider.calls) == 4 and len(posted) == 1
        assert posted[0]["commit_id"] == HEAD
        assert "already complete" not in summary


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"viewer": None}},
        {"data": {"viewer": {"login": ""}}},
        {"data": {"viewer": {"login": "pat-user"}}, "errors": [{"message": "refused"}]},
    ],
)
def test_unknown_authenticated_identity_never_reuses_or_updates_a_summary(payload: dict) -> None:
    writes = []

    def handle(request):
        if request.method == "POST" and request.url.path == "/graphql":
            return httpx.Response(200, json=payload)
        if request.method == "GET" and request.url.path.endswith("/reviews"):
            return httpx.Response(200, json=[_review(100, HEAD, "untrusted-pr-reviewer")])
        writes.append(request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = RestGitHubGateway(
            "owner/repo", 42, "synthetic", client=client, marker_key="openrouter/test"
        )
        with pytest.raises(RuntimeError):
            gateway.last_completed_sha(diagram_required=False)
        gateway.mark_reviewed(HEAD)
        with pytest.raises(RuntimeError):
            gateway.post_review([], "Complete summary")
    assert writes == []


@pytest.fixture
def _authenticated_posting_identity():
    # These cases own the actual HTTP identity responses, including refusals.
    yield


@pytest.mark.parametrize(
    "diagram_actor, expected", [("untrusted-pr-reviewer", None), ("github-actions[bot]", HEAD)]
)
def test_completed_diagram_must_belong_to_the_authenticated_actor(
    diagram_actor: str, expected: str | None
) -> None:
    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"viewer": {"login": "github-actions[bot]"}}})
        if request.url.path.endswith("/reviews"):
            return httpx.Response(200, json=[_review(99, OLD, "github-actions[bot]")])
        assert request.url.path.endswith("/comments")
        return httpx.Response(
            200,
            json=[
                {
                    "id": 100,
                    "body": (
                        "<!-- lgtmaybe-diagram:openrouter/test -->\n"
                        f"<!-- lgtmaybe-diagrammed:{HEAD} -->"
                    ),
                    "user": {"login": diagram_actor},
                }
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = RestGitHubGateway(
            "owner/repo", 42, "synthetic", client=client, marker_key="openrouter/test"
        )
        assert gateway.last_completed_sha(diagram_required=True) == expected


def test_untrusted_latest_marker_cannot_redirect_an_authenticated_summary_update() -> None:
    writes = []

    def handle(request):
        if request.method == "POST" and request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"viewer": {"login": "github-actions[bot]"}}})
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    _review(99, HEAD, "github-actions[bot]"),
                    _review(100, HEAD, "untrusted-pr-reviewer"),
                ],
            )
        writes.append(request.url.path)
        assert request.method == "PUT" and request.url.path.endswith("/99")
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        gateway = RestGitHubGateway(
            "owner/repo",
            42,
            "synthetic",
            client=client,
            marker_key="openrouter/test",
            resolve_fixed=False,
        )
        gateway.mark_reviewed(HEAD)
        gateway.post_review([], "Authenticated summary")
    assert writes == ["/repos/owner/repo/pulls/42/reviews/99"]
