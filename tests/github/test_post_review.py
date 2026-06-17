"""Tests for RestGitHubGateway.post_review — idempotency and batching."""

from __future__ import annotations

import json

import httpx
import respx

from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.github import RestGitHubGateway
from lgtmaybe.github.rest_gateway import finding_fingerprint

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"

BASE_URL = "https://api.github.com"
PR_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}"
REVIEWS_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/reviews"
COMMENTS_URL = f"{BASE_URL}/repos/{REPO}/issues/{PR_NUMBER}/comments"
GRAPHQL_URL = f"{BASE_URL}/graphql"

MARKER = "<!-- lgtmaybe -->"

# A minimal diff in which src/app.py new-file line 2 ("+import sys") is an
# added line, so a RIGHT-side finding on line 2 anchors there.
SAMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 0000001..0000002 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,6 @@
 import os
+import sys

 def main():
-    pass
+    print("hello")
+    return 0
"""

FINDINGS = [
    ReviewFinding(
        path="src/app.py",
        line=2,
        side="RIGHT",
        severity=Severity.medium,
        title="Import order",
        body="sys should be before os",
        suggestion=None,
    )
]


def _pr_detail() -> dict[object, object]:
    return {"number": PR_NUMBER, "base": {"sha": "abc"}, "head": {"sha": "def"}}


@respx.mock
def test_post_review_creates_review_with_marker_and_batched_comments() -> None:
    """First post_review call creates one review containing the marker."""
    # No existing reviews
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))
    created_bodies: list[dict[object, object]] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        created_bodies.append(body)
        return httpx.Response(201, json={"id": 1, "body": body.get("body", "")})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    gw.post_review(FINDINGS, "Summary text", diff=SAMPLE_DIFF)

    assert len(created_bodies) == 1
    review_body = created_bodies[0]
    assert MARKER in str(review_body.get("body", ""))
    assert review_body.get("event") == "COMMENT"
    comments = review_body.get("comments", [])
    assert len(comments) == 1
    assert comments[0]["path"] == "src/app.py"
    assert comments[0]["line"] == 2
    assert comments[0]["side"] == "RIGHT"
    assert "position" not in comments[0]


@respx.mock
def test_post_review_updates_existing_review_on_second_call() -> None:
    """Second post_review call updates the existing review rather than creating another."""
    existing_review_id = 99
    existing_reviews = [{"id": existing_review_id, "body": f"Old summary {MARKER}"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(
        return_value=httpx.Response(200, json=existing_reviews)
    )

    update_url = f"{REVIEWS_URL}/{existing_review_id}"
    updated_bodies: list[dict[object, object]] = []

    def capture_update(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        updated_bodies.append(body)
        return httpx.Response(200, json={"id": existing_review_id, "body": body.get("body", "")})

    create_calls: list[httpx.Request] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        create_calls.append(request)
        return httpx.Response(201, json={"id": 100})

    respx.route(method="PUT", url=update_url).mock(side_effect=capture_update)
    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    # Must have updated, not created
    assert len(create_calls) == 0, "Should not POST a new review when one already exists"
    assert len(updated_bodies) == 1
    assert MARKER in str(updated_bodies[0].get("body", ""))


@respx.mock
def test_post_review_finds_existing_review_past_first_page() -> None:
    """The marker review is found even when it sits beyond the first page of
    reviews (GitHub returns 30 per page), so a busy PR still gets its review
    updated in place rather than duplicated."""
    existing_review_id = 99
    page1 = [{"id": i, "body": f"human review {i}"} for i in range(30)]
    page2 = [{"id": existing_review_id, "body": f"Old summary {MARKER}"}]
    page2_url = f"{REVIEWS_URL}?page=2"

    # One route serves both pages in order: a respx URL pattern without a query
    # string matches any query, so registering page 2 separately would be
    # shadowed by the page-1 route.
    respx.route(method="GET", url=REVIEWS_URL).mock(
        side_effect=[
            httpx.Response(200, json=page1, headers={"Link": f'<{page2_url}>; rel="next"'}),
            httpx.Response(200, json=page2),
        ]
    )

    update_url = f"{REVIEWS_URL}/{existing_review_id}"
    updated_bodies: list[dict[object, object]] = []

    def capture_update(request: httpx.Request) -> httpx.Response:
        updated_bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": existing_review_id})

    create_calls: list[httpx.Request] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        create_calls.append(request)
        return httpx.Response(201, json={"id": 100})

    respx.route(method="PUT", url=update_url).mock(side_effect=capture_update)
    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    assert len(create_calls) == 0, "must not duplicate a review that exists on a later page"
    assert len(updated_bodies) == 1
    assert MARKER in str(updated_bodies[0].get("body", ""))


@respx.mock
def test_post_issue_comment_posts_to_issues_endpoint() -> None:
    """post_issue_comment posts a standalone comment to the PR conversation."""
    posted: list[dict[object, object]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    respx.route(method="POST", url=COMMENTS_URL).mock(side_effect=capture)

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    gw.post_issue_comment("Because it guards against null.")

    assert len(posted) == 1
    assert posted[0]["body"] == "Because it guards against null."


@respx.mock
def test_post_review_skips_findings_outside_diff() -> None:
    """Findings whose line is not in the diff are omitted from the review comments."""
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))

    out_of_diff_findings = [
        ReviewFinding(
            path="src/app.py",
            line=999,  # not in the diff
            side="RIGHT",
            severity=Severity.high,
            title="Not in diff",
            body="This line is not in the diff",
            suggestion=None,
        )
    ]

    created_bodies: list[dict[object, object]] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        created_bodies.append(body)
        return httpx.Response(201, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    gw.post_review(out_of_diff_findings, "Summary", diff=SAMPLE_DIFF)

    assert len(created_bodies) == 1
    assert created_bodies[0].get("comments", []) == []


@respx.mock
def test_post_review_drops_finding_on_expanded_context_line() -> None:
    """A finding on a surrounding-context line (not in the real diff) is never posted.

    The engine pads hunks with extra lines for the model, but the commentable-line
    index is built from the real diff — so a finding landing on an expanded-only
    line has no anchor and is dropped, never producing a bogus inline comment.
    """
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))

    # SAMPLE_DIFF's hunk covers new-file lines 1..6; line 20 would only ever be
    # visible as expanded surrounding context, not in the diff itself.
    findings = [
        ReviewFinding(
            path="src/app.py",
            line=20,
            severity=Severity.high,
            title="On expanded context",
            body="Only visible via context expansion",
        )
    ]

    created_bodies: list[dict[object, object]] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        created_bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(findings, "Summary", diff=SAMPLE_DIFF)

    assert created_bodies[0].get("comments", []) == []


@respx.mock
def test_post_review_suggestion_cannot_break_out_of_code_fence() -> None:
    """A model-emitted suggestion containing ``` must not escape the suggestion
    fence and inject markdown (e.g. a phishing link) below it.

    The diff is attacker-controlled on a fork PR, so a prompt injection that
    survives the guard could steer the model into emitting fence-breaking output.
    We neutralise embedded triple-backticks so only our own open/close fences
    remain.
    """
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))

    malicious = [
        ReviewFinding(
            path="src/app.py",
            line=2,
            side="RIGHT",
            severity=Severity.medium,
            title="x",
            body="x",
            suggestion="legit_code()\n```\n[click me](https://evil.example)\n```",
        )
    ]

    created_bodies: list[dict[object, object]] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        created_bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(malicious, "Summary", diff=SAMPLE_DIFF)

    comment_body = created_bodies[0]["comments"][0]["body"]
    # Exactly our two fences (the ```suggestion opener and its closer) — the
    # attacker's embedded ``` runs no longer read as fence delimiters.
    assert comment_body.count("```") == 2


@respx.mock
def test_post_review_uses_provider_scoped_marker() -> None:
    """A gateway built with a marker_key embeds a provider/model-scoped marker."""
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))

    created_bodies: list[dict[object, object]] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        created_bodies.append(body)
        return httpx.Response(201, json={"id": 1, "body": body.get("body", "")})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    gw = RestGitHubGateway(
        repo=REPO,
        pr_number=PR_NUMBER,
        token=TOKEN,
        client=httpx.Client(),
        marker_key="openai/gpt-4.1-mini",
    )
    gw.post_review(FINDINGS, "Summary text", diff=SAMPLE_DIFF)

    assert "<!-- lgtmaybe:openai/gpt-4.1-mini -->" in str(created_bodies[0].get("body", ""))


@respx.mock
def test_post_review_with_distinct_marker_keys_coexist() -> None:
    """A review from another provider/model is left intact; a gateway with a
    different marker_key creates its own review instead of overwriting it."""
    other = [{"id": 7, "body": "Anthropic summary <!-- lgtmaybe:anthropic/claude-haiku-4-5 -->"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=other))

    create_bodies: list[dict[object, object]] = []
    put_calls: list[httpx.Request] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        create_bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 8})

    def capture_put(request: httpx.Request) -> httpx.Response:
        put_calls.append(request)
        return httpx.Response(200, json={"id": 7})

    respx.route(method="PUT").mock(side_effect=capture_put)
    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    gw = RestGitHubGateway(
        repo=REPO,
        pr_number=PR_NUMBER,
        token=TOKEN,
        client=httpx.Client(),
        marker_key="openai/gpt-4.1-mini",
    )
    gw.post_review(FINDINGS, "OpenAI summary", diff=SAMPLE_DIFF)

    assert len(put_calls) == 0, "must not overwrite another provider's review"
    assert len(create_bodies) == 1
    assert "<!-- lgtmaybe:openai/gpt-4.1-mini -->" in str(create_bodies[0].get("body", ""))


# ---------------------------------------------------------------------------
# Fingerprinting + auto-resolve of fixed conversations
# ---------------------------------------------------------------------------


def _mark_existing_review() -> None:
    """Route the reviews GET so an existing lgtmaybe review is found (a re-run)
    and the PUT that updates its summary succeeds."""
    existing = [{"id": 99, "body": f"Old summary {MARKER}"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))
    respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )


class _GraphQL:
    """Records GraphQL calls and replies based on the operation in the query."""

    def __init__(self, threads: list[dict[object, object]]) -> None:
        self._threads = threads
        self.replies: list[dict[str, object]] = []
        self.resolved: list[str] = []
        self.queried = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        query = payload["query"]
        variables = payload.get("variables", {})
        if "reviewThreads" in query:
            self.queried = True
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": self._threads,
                                }
                            }
                        }
                    }
                },
            )
        if "addPullRequestReviewThreadReply" in query:
            self.replies.append(variables)
            return httpx.Response(200, json={"data": {"addPullRequestReviewThreadReply": {}}})
        if "resolveReviewThread" in query:
            self.resolved.append(variables["threadId"])
            return httpx.Response(200, json={"data": {"resolveReviewThread": {}}})
        raise AssertionError(f"unexpected GraphQL operation: {query}")


def _thread(fingerprint: str, *, outdated: bool, resolved: bool = False, tid: str = "T1") -> dict:
    body = f"**[MEDIUM] Old issue**\n\nbody\n\n<!-- lgtmaybe-finding:{fingerprint} -->"
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": outdated,
        "comments": {"nodes": [{"body": body}]},
    }


@respx.mock
def test_post_review_embeds_finding_fingerprint() -> None:
    """Each inline comment carries a hidden fingerprint marker so a later run can
    tell which conversation a finding belongs to."""
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))
    created_bodies: list[dict[object, object]] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        created_bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "Summary text", diff=SAMPLE_DIFF)

    fp = finding_fingerprint("src/app.py", "Import order")
    body = created_bodies[0]["comments"][0]["body"]
    assert f"<!-- lgtmaybe-finding:{fp} -->" in body


@respx.mock
def test_resolve_fixed_resolves_outdated_disappeared_thread() -> None:
    """A prior lgtmaybe thread whose finding is gone AND whose code is outdated is
    replied to and resolved."""
    _mark_existing_review()
    gone_fp = finding_fingerprint("src/old.py", "Removed bug")
    graphql = _GraphQL([_thread(gone_fp, outdated=True, tid="THREAD1")])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    assert graphql.resolved == ["THREAD1"]
    assert len(graphql.replies) == 1
    assert graphql.replies[0]["threadId"] == "THREAD1"


@respx.mock
def test_resolve_fixed_skips_still_present_finding() -> None:
    """A thread whose finding is still produced this run is left open."""
    _mark_existing_review()
    present_fp = finding_fingerprint("src/app.py", "Import order")  # matches FINDINGS
    graphql = _GraphQL([_thread(present_fp, outdated=True, tid="THREAD1")])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    assert graphql.resolved == []
    assert graphql.replies == []


@respx.mock
def test_resolve_fixed_skips_non_outdated_thread() -> None:
    """A disappeared finding whose thread is NOT outdated is left open — the code
    under it didn't change, so we can't conclude it was fixed."""
    _mark_existing_review()
    gone_fp = finding_fingerprint("src/old.py", "Removed bug")
    graphql = _GraphQL([_thread(gone_fp, outdated=False, tid="THREAD1")])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    assert graphql.resolved == []


@respx.mock
def test_resolve_fixed_skips_already_resolved_thread() -> None:
    """An already-resolved thread is never re-resolved."""
    _mark_existing_review()
    gone_fp = finding_fingerprint("src/old.py", "Removed bug")
    graphql = _GraphQL([_thread(gone_fp, outdated=True, resolved=True, tid="THREAD1")])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    assert graphql.resolved == []


@respx.mock
def test_resolve_fixed_ignores_non_lgtmaybe_threads() -> None:
    """A human review thread (no fingerprint marker) is never touched."""
    _mark_existing_review()
    human_thread = {
        "id": "HUMAN",
        "isResolved": False,
        "isOutdated": True,
        "comments": {"nodes": [{"body": "please rename this variable"}]},
    }
    graphql = _GraphQL([human_thread])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    assert graphql.resolved == []


@respx.mock
def test_resolve_fixed_disabled_makes_no_graphql_call() -> None:
    """With resolve_fixed=False, no GraphQL request is made at all."""
    _mark_existing_review()
    gone_fp = finding_fingerprint("src/old.py", "Removed bug")
    graphql = _GraphQL([_thread(gone_fp, outdated=True, tid="THREAD1")])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    gw = RestGitHubGateway(
        repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client(), resolve_fixed=False
    )
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)

    assert graphql.queried is False
    assert graphql.resolved == []


@respx.mock
def test_resolve_fixed_skipped_on_first_review() -> None:
    """On the first review (no existing one) there are no prior threads, so the
    resolve pass is not even attempted."""
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))
    respx.route(method="POST", url=REVIEWS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    graphql = _GraphQL([])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.post_review(FINDINGS, "Summary", diff=SAMPLE_DIFF)

    assert graphql.queried is False


@respx.mock
def test_resolve_fixed_swallows_graphql_error() -> None:
    """A GraphQL failure during the resolve pass never fails the review."""
    _mark_existing_review()
    respx.route(method="POST", url=GRAPHQL_URL).mock(return_value=httpx.Response(500))

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    # Must not raise.
    gw.post_review(FINDINGS, "New summary", diff=SAMPLE_DIFF)


@respx.mock
def test_unanchored_finding_demoted_to_body_not_inline() -> None:
    """A finding flagged anchored=False is never posted inline on a guessed line;
    it is appended to the review body so the signal survives without a wrong anchor."""
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))
    created_bodies: list[dict[object, object]] = []

    def capture_create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        created_bodies.append(body)
        return httpx.Response(201, json={"id": 1, "body": body.get("body", "")})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)

    findings = [
        ReviewFinding(
            path="src/app.py",
            line=2,
            side="RIGHT",
            severity=Severity.high,
            title="Anchored finding",
            body="this one is placed inline",
        ),
        ReviewFinding(
            path="src/app.py",
            line=2,  # a real changed line, but we could not anchor it — must NOT post inline
            side="RIGHT",
            severity=Severity.high,
            title="Unplaced finding",
            body="this one has no trustworthy line",
            anchored=False,
        ),
    ]

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    gw.post_review(findings, "Summary text", diff=SAMPLE_DIFF)

    body = created_bodies[0]
    comments = body.get("comments", [])
    titles_inline = [c["body"] for c in comments]
    assert len(comments) == 1
    assert all("Unplaced finding" not in c for c in titles_inline)
    # The demoted finding survives in the review body instead.
    rendered = str(body.get("body", ""))
    assert "Unplaced finding" in rendered
    # Framed as a feature for the reader, not an apology about our internals:
    # no pipeline jargon ("anchor") leaks into customer-facing copy.
    assert "### Additional findings" in rendered
    assert "anchor" not in rendered.lower()
    assert "Couldn't anchor" not in rendered
