"""Gateway primitives for commit-scoped incremental review (P2).

Four contracts, all respx-mocked:

- ``post_review`` stamps a hidden ``<!-- lgtmaybe-reviewed:<head_sha> -->``
  marker into the summary body so the next run knows where the last review
  stopped;
- ``last_reviewed_sha`` reads that marker back from the existing review;
- ``compare_diff`` returns the unified diff of ``last...head`` when head is
  strictly ahead (a normal push), and None on a force-push/rebase (diverged),
  an identical compare, or any API failure — the caller then falls back to a
  full review;
- ``set_incremental_scope`` restricts resolve-on-fix to threads on paths
  inside the increment, so a finding that simply wasn't re-reviewed this run
  is never spuriously resolved.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lgtmaybe.core.findings import finding_fingerprint
from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.github import RestGitHubGateway

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"

BASE_URL = "https://api.github.com"
PR_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}"
REVIEWS_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/reviews"
REVIEW_COMMENTS_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/comments"
ISSUE_COMMENTS_URL = f"{BASE_URL}/repos/{REPO}/issues/{PR_NUMBER}/comments"
GRAPHQL_URL = f"{BASE_URL}/graphql"

MARKER = "<!-- lgtmaybe -->"

SAMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 0000001..0000002 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 import os
+import sys
 x = 1
"""

FINDING = ReviewFinding(
    path="src/app.py",
    line=2,
    side="RIGHT",
    severity=Severity.medium,
    title="Import order",
    body="sys should be before os",
)

SECOND_FINDING = ReviewFinding(
    path="src/app.py",
    line=3,
    side="RIGHT",
    severity=Severity.high,
    title="Validate the imported module",
    body="the second finding must still reach GitHub",
)

TWO_FINDING_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 0000001..0000002 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,3 @@
 import os
+import sys
+import app
"""


def _gateway() -> RestGitHubGateway:
    return RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN)


# ---------------------------------------------------------------------------
# reviewed-SHA marker: stamped on post, read back on the next run
# ---------------------------------------------------------------------------


@respx.mock
def test_post_review_stamps_last_reviewed_sha_marker_when_marked() -> None:
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture)

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert "<!-- lgtmaybe-reviewed:headsha123 -->" in str(captured["body"])


@respx.mock
def test_unmarked_post_does_not_stamp_a_watermark() -> None:
    """A failure notice posts through post_review WITHOUT mark_reviewed — it
    must not record 'reviewed up to head' (the next run would skip commits
    nobody reviewed), and replacing the body clears any stale stamp."""
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture)

    _gateway().post_review([], "⚠️ lgtmaybe review failed: boom", diff=SAMPLE_DIFF)

    assert "lgtmaybe-reviewed" not in str(captured["body"])


@respx.mock
def test_mark_reviewed_none_clears_watermark() -> None:
    """The CLI's failure path calls mark_reviewed(None) to clear a previously
    set watermark, so a failure notice posted after a partial run never stamps
    <!-- lgtmaybe-reviewed:... --> for commits nobody fully reviewed."""
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture)

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")
    gateway.mark_reviewed(None)
    gateway.post_review([], "⚠️ lgtmaybe review failed: boom", diff=SAMPLE_DIFF)

    assert "lgtmaybe-reviewed" not in str(captured["body"])


@respx.mock
def test_last_reviewed_sha_reads_marker_from_existing_review() -> None:
    existing = [{"id": 7, "body": f"Old summary\n\n{MARKER}\n<!-- lgtmaybe-reviewed:cafe1234 -->"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))

    assert _gateway().last_reviewed_sha() == "cafe1234"


@respx.mock
def test_last_reviewed_sha_none_without_marker() -> None:
    existing = [{"id": 7, "body": f"Old summary {MARKER}"}]  # pre-P2 review: no SHA stamp
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))

    assert _gateway().last_reviewed_sha() is None


@respx.mock
def test_last_reviewed_sha_none_without_existing_review() -> None:
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))

    assert _gateway().last_reviewed_sha() is None


@respx.mock
def test_last_reviewed_sha_none_on_api_error() -> None:
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(500))

    assert _gateway().last_reviewed_sha() is None


@respx.mock
def test_diagrammed_head_is_the_completed_head_when_required() -> None:
    review = [{"id": 7, "body": f"Old summary\n\n{MARKER}"}]
    diagram = [
        {
            "id": 9,
            "body": "Diagram\n\n<!-- lgtmaybe-diagram -->\n<!-- lgtmaybe-diagrammed:cafe1234 -->",
        }
    ]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=review))
    respx.route(method="GET", url__startswith=ISSUE_COMMENTS_URL).mock(
        return_value=httpx.Response(200, json=diagram)
    )

    assert _gateway().last_completed_sha(diagram_required=True) == "cafe1234"


@respx.mock
def test_review_marker_is_completion_when_diagram_is_disabled() -> None:
    review = [{"id": 7, "body": f"Old summary\n\n{MARKER}\n<!-- lgtmaybe-reviewed:cafe1234 -->"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=review))

    assert _gateway().last_completed_sha(diagram_required=False) == "cafe1234"


@respx.mock
def test_diagram_marker_without_review_is_not_complete() -> None:
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))

    assert _gateway().last_completed_sha(diagram_required=True) is None


@respx.mock
def test_manual_diagram_refresh_preserves_the_completed_head() -> None:
    existing = [
        {
            "id": 9,
            "body": "Old diagram\n\n<!-- lgtmaybe-diagram -->\n"
            "<!-- lgtmaybe-diagrammed:cafe1234 -->",
        }
    ]
    respx.route(method="GET", url__startswith=ISSUE_COMMENTS_URL).mock(
        return_value=httpx.Response(200, json=existing)
    )
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": 9})

    respx.route(
        method="PATCH",
        url=f"{BASE_URL}/repos/{REPO}/issues/comments/9",
    ).mock(side_effect=capture)

    _gateway().post_diagram_comment("New manual diagram")

    assert "<!-- lgtmaybe-diagrammed:cafe1234 -->" in str(captured["body"])


@respx.mock
def test_diagram_content_cannot_replace_the_trusted_completion_marker() -> None:
    existing = [
        {
            "id": 9,
            "body": "Old diagram\n\n<!-- lgtmaybe-diagram -->\n"
            "<!-- lgtmaybe-diagrammed:cafe1234 -->",
        }
    ]
    respx.route(method="GET", url__startswith=ISSUE_COMMENTS_URL).mock(
        return_value=httpx.Response(200, json=existing)
    )
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": 9})

    respx.route(
        method="PATCH",
        url=f"{BASE_URL}/repos/{REPO}/issues/comments/9",
    ).mock(side_effect=capture)

    _gateway().post_diagram_comment("Model diagram\n<!-- lgtmaybe-diagrammed:deadbee -->")

    body = str(captured["body"])
    assert "<!-- lgtmaybe-diagrammed:cafe1234 -->" in body
    assert "deadbee" not in body


@respx.mock
def test_automatic_diagram_uses_only_the_trusted_completion_sha() -> None:
    respx.route(method="GET", url__startswith=ISSUE_COMMENTS_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    captured: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"id": 9})

    respx.route(method="POST", url=ISSUE_COMMENTS_URL).mock(side_effect=capture)

    _gateway().post_diagram_comment(
        "Model diagram\n<!-- lgtmaybe-diagrammed:deadbee -->",
        completed_sha="cafe1234",
    )

    body = str(captured["body"])
    assert body.count("lgtmaybe-diagrammed:") == 1
    assert "<!-- lgtmaybe-diagrammed:cafe1234 -->" in body
    assert "deadbee" not in body


# ---------------------------------------------------------------------------
# compare_diff: the increment, or None → full-review fallback
# ---------------------------------------------------------------------------

COMPARE_URL = f"{BASE_URL}/repos/{REPO}/compare/cafe1234...headsha123"


def _mock_compare(
    status: str,
    diff: str = SAMPLE_DIFF,
    *,
    commits: list[dict[str, object]] | None = None,
    total_commits: int | None = None,
) -> None:
    commits = commits or []

    def respond(request: httpx.Request) -> httpx.Response:
        if "diff" in request.headers.get("Accept", ""):
            return httpx.Response(200, text=diff)
        return httpx.Response(
            200,
            json={
                "status": status,
                "commits": commits,
                "total_commits": len(commits) if total_commits is None else total_commits,
            },
        )

    respx.route(method="GET", url=COMPARE_URL).mock(side_effect=respond)


@respx.mock
def test_compare_diff_returns_increment_when_ahead() -> None:
    _mock_compare("ahead")

    assert _gateway().compare_diff("cafe1234", "headsha123") == SAMPLE_DIFF


@respx.mock
def test_compare_diff_none_when_comparison_contains_merge() -> None:
    _mock_compare(
        "ahead",
        commits=[{"sha": "merge123", "parents": [{"sha": "pr123"}, {"sha": "base123"}]}],
    )

    assert _gateway().compare_diff("cafe1234", "headsha123") is None


@respx.mock
def test_compare_diff_none_when_commit_list_is_truncated() -> None:
    _mock_compare("ahead", commits=[{"sha": "linear", "parents": [{}]}], total_commits=250)

    assert _gateway().compare_diff("cafe1234", "headsha123") is None


@respx.mock
def test_compare_diff_none_when_diverged() -> None:
    """A force-push/rebase makes the last-reviewed SHA diverge from head — the
    increment is meaningless, so the caller must fall back to a full review."""
    _mock_compare("diverged")

    assert _gateway().compare_diff("cafe1234", "headsha123") is None


@respx.mock
def test_compare_diff_none_when_identical() -> None:
    _mock_compare("identical")

    assert _gateway().compare_diff("cafe1234", "headsha123") is None


@respx.mock
def test_compare_diff_none_on_api_error() -> None:
    """A GC'd SHA after a force-push 404s — degrade to full review, never raise."""
    respx.route(method="GET", url=COMPARE_URL).mock(return_value=httpx.Response(404))

    assert _gateway().compare_diff("cafe1234", "headsha123") is None


# ---------------------------------------------------------------------------
# update path posts NEW inline comments (deduped by fingerprint)
# ---------------------------------------------------------------------------


def _mock_update_flow(
    existing_comment_fps: list[str],
) -> tuple[list[dict[str, object]], respx.Route]:
    """Mock a re-run: existing review found, PUT body, list review comments,
    capture POSTed new comments. Returns (captured_new_comments, put_route)."""
    existing = [{"id": 99, "body": f"Old summary {MARKER}"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))
    put_route = respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )
    existing_comments = [
        {"id": i, "body": f"stuff\n\n<!-- lgtmaybe-finding:{fp} -->"}
        for i, fp in enumerate(existing_comment_fps)
    ]
    respx.route(method="GET", url=REVIEW_COMMENTS_URL + "?per_page=100").mock(
        return_value=httpx.Response(200, json=existing_comments)
    )
    posted: list[dict[str, object]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1000 + len(posted)})

    respx.route(method="POST", url=REVIEW_COMMENTS_URL).mock(side_effect=capture)
    return posted, put_route


@respx.mock
def test_rerun_posts_new_inline_comment_with_commit_id() -> None:
    posted, put_route = _mock_update_flow(existing_comment_fps=[])

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert put_route.called  # body still updated in place
    assert len(posted) == 1
    assert posted[0]["path"] == "src/app.py"
    assert posted[0]["line"] == 2
    assert posted[0]["side"] == "RIGHT"
    assert posted[0]["commit_id"] == "headsha123"


@respx.mock
def test_rerun_demotes_a_422_comment_and_posts_later_findings() -> None:
    existing = [{"id": 99, "body": f"Old summary {MARKER}"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))
    updated_bodies: list[str] = []

    def capture_update(request: httpx.Request) -> httpx.Response:
        updated_bodies.append(json.loads(request.content)["body"])
        return httpx.Response(200, json={"id": 99})

    respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(side_effect=capture_update)
    respx.route(method="GET", url=REVIEW_COMMENTS_URL + "?per_page=100").mock(
        return_value=httpx.Response(200, json=[])
    )
    posted_paths: list[str] = []

    def reject_then_accept(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        posted_paths.append(payload["path"] + f":{payload['line']}")
        if len(posted_paths) == 1:
            return httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [{"field": "line", "code": "invalid"}],
                },
            )
        return httpx.Response(201, json={"id": 1000})

    respx.route(method="POST", url=REVIEW_COMMENTS_URL).mock(side_effect=reject_then_accept)

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")
    gateway.post_review([FINDING, SECOND_FINDING], "2 findings", diff=TWO_FINDING_DIFF)

    assert posted_paths == ["src/app.py:2", "src/app.py:3"]
    assert len(updated_bodies) == 2
    assert "### Additional findings" in updated_bodies[-1]
    assert "Import order" in updated_bodies[-1]
    assert "Validate the imported module" not in updated_bodies[-1]


@respx.mock
def test_rerun_logs_sanitized_422_validation_details(caplog: pytest.LogCaptureFixture) -> None:
    _mock_update_flow(existing_comment_fps=[])
    validation_error = respx.route(method="POST", url=REVIEW_COMMENTS_URL).mock(
        return_value=httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "PullRequestReviewComment",
                        "field": "line",
                        "value": FINDING.body,
                    }
                ],
            },
        )
    )

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert validation_error.called
    assert "src/app.py:2:RIGHT" in caplog.text
    assert "Validation Failed" in caplog.text
    assert "PullRequestReviewComment" in caplog.text
    assert FINDING.body not in caplog.text
    assert TOKEN not in caplog.text


@respx.mock
def test_rerun_keeps_non_422_comment_errors_fatal() -> None:
    _mock_update_flow(existing_comment_fps=[])
    respx.route(method="POST", url=REVIEW_COMMENTS_URL).mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")

    with pytest.raises(httpx.HTTPStatusError):
        gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)


@respx.mock
def test_rerun_fails_when_the_recovery_body_cannot_be_updated() -> None:
    existing = [{"id": 99, "body": f"Old summary {MARKER}"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))
    update_attempts = 0

    def accept_then_reject_update(_request: httpx.Request) -> httpx.Response:
        nonlocal update_attempts
        update_attempts += 1
        return httpx.Response(200 if update_attempts == 1 else 500, json={"id": 99})

    respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(side_effect=accept_then_reject_update)
    respx.route(method="GET", url=REVIEW_COMMENTS_URL + "?per_page=100").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.route(method="POST", url=REVIEW_COMMENTS_URL).mock(
        return_value=httpx.Response(422, json={"message": "Validation Failed"})
    )

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")

    with pytest.raises(httpx.HTTPStatusError):
        gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert update_attempts == 2


@respx.mock
def test_rerun_skips_inline_comment_already_posted() -> None:
    fp = finding_fingerprint(FINDING.path, FINDING.title)
    posted, _put = _mock_update_flow(existing_comment_fps=[fp])

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert posted == []


@respx.mock
def test_rerun_reposts_finding_whose_thread_was_resolved_as_fixed() -> None:
    """A comment whose marker was rewritten to the "resolved" family (by the
    resolve-on-fix pass) no longer counts as an existing finding — the same
    finding reintroduced later posts again instead of being suppressed forever."""
    fp = finding_fingerprint(FINDING.path, FINDING.title)
    existing = [{"id": 99, "body": f"Old summary {MARKER}"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))
    respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )
    resolved_comment = [{"id": 1, "body": f"stuff\n\n<!-- lgtmaybe-resolved-fingerprint:{fp} -->"}]
    respx.route(method="GET", url=REVIEW_COMMENTS_URL + "?per_page=100").mock(
        return_value=httpx.Response(200, json=resolved_comment)
    )
    posted: list[dict[str, object]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1000})

    respx.route(method="POST", url=REVIEW_COMMENTS_URL).mock(side_effect=capture)

    gateway = _gateway()
    gateway.mark_reviewed("headsha123")
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert len(posted) == 1
    assert posted[0]["path"] == "src/app.py"


@respx.mock
def test_incomplete_rerun_still_posts_new_inline_findings() -> None:
    """#443: an incomplete re-run carries no completion watermark
    (``mark_reviewed(None)``), but its successful calls still computed findings
    and the summary still counts them — they must reach GitHub inline, anchored
    to the PR's current head, rather than vanish between count and post."""
    posted, put_route = _mock_update_flow(existing_comment_fps=[])
    respx.route(method="GET", url=PR_URL).mock(
        return_value=httpx.Response(200, json={"head": {"sha": "headsha999"}})
    )

    gateway = _gateway()
    gateway.mark_reviewed(None)
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert put_route.called  # summary still updated in place
    assert len(posted) == 1
    assert posted[0]["commit_id"] == "headsha999"
    assert posted[0]["path"] == "src/app.py"


@respx.mock
def test_rerun_without_any_resolvable_head_demotes_new_findings_to_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The anchor-less fallback must never silently drop findings (#443): when
    no head SHA can be resolved at all, the unmatched candidates demote into
    the review body — the same recovery as a 422 — with a warning."""
    existing = [{"id": 99, "body": f"Old summary {MARKER}"}]
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=existing))
    updated_bodies: list[str] = []

    def capture_update(request: httpx.Request) -> httpx.Response:
        updated_bodies.append(json.loads(request.content)["body"])
        return httpx.Response(200, json={"id": 99})

    respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(side_effect=capture_update)
    respx.route(method="GET", url=REVIEW_COMMENTS_URL + "?per_page=100").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.route(method="GET", url=PR_URL).mock(return_value=httpx.Response(503))

    gateway = _gateway()
    gateway.mark_reviewed(None)
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert len(updated_bodies) == 2  # recovery body re-PUT with the demoted finding
    assert "### Additional findings" in updated_bodies[-1]
    assert "Import order" in updated_bodies[-1]
    assert "demoting" in caplog.text


# ---------------------------------------------------------------------------
# incremental scope on resolve-on-fix
# ---------------------------------------------------------------------------


def _thread(fingerprint: str, path: str, tid: str) -> dict[str, object]:
    return {
        "id": tid,
        "isResolved": False,
        "isOutdated": True,
        "path": path,
        "comments": {
            "nodes": [{"body": f"x <!-- lgtmaybe-finding:{fingerprint} -->", "databaseId": 555}]
        },
    }


class _GraphQL:
    """Route the GraphQL endpoint: serve threads, record resolutions."""

    def __init__(self, threads: list[dict[str, object]]) -> None:
        self._threads = threads
        self.resolved: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        query: str = payload["query"]
        if "reviewThreads" in query:
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
            return httpx.Response(200, json={"data": {"addPullRequestReviewThreadReply": {}}})
        if "unresolveReviewThread" in query:
            return httpx.Response(200, json={"data": {"unresolveReviewThread": {}}})
        if "resolveReviewThread" in query:
            self.resolved.append(payload["variables"]["threadId"])
            return httpx.Response(200, json={"data": {"resolveReviewThread": {}}})
        raise AssertionError(f"unexpected GraphQL query: {query}")


@respx.mock
def test_incremental_scope_never_resolves_thread_outside_reviewed_paths() -> None:
    """A finding on an un-re-reviewed file is absent from this run's findings
    only because its hunk wasn't in the increment — its thread must stay open."""
    posted, _put = _mock_update_flow(existing_comment_fps=[])
    respx.route(method="GET", url=PR_URL).mock(
        return_value=httpx.Response(200, json={"head": {"sha": "headsha999"}})
    )
    graphql = _GraphQL(
        threads=[
            _thread(finding_fingerprint("other.py", "old bug"), path="other.py", tid="OUT"),
            _thread(finding_fingerprint("src/app.py", "gone bug"), path="src/app.py", tid="IN"),
        ]
    )
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)
    respx.route(method="PATCH").mock(return_value=httpx.Response(200, json={}))

    gateway = _gateway()
    gateway.set_incremental_scope({"src/app.py"})
    gateway.post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    # The in-scope gone finding resolves; the out-of-scope one is untouched.
    assert graphql.resolved == ["IN"]


@respx.mock
def test_no_scope_keeps_full_resolve_behaviour() -> None:
    posted, _put = _mock_update_flow(existing_comment_fps=[])
    respx.route(method="GET", url=PR_URL).mock(
        return_value=httpx.Response(200, json={"head": {"sha": "headsha999"}})
    )
    graphql = _GraphQL(
        threads=[_thread(finding_fingerprint("other.py", "old bug"), path="other.py", tid="T1")]
    )
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)
    respx.route(method="PATCH").mock(return_value=httpx.Response(200, json={}))

    _gateway().post_review([FINDING], "1 finding", diff=SAMPLE_DIFF)

    assert graphql.resolved == ["T1"]


# ---------------------------------------------------------------------------
# feedback learning: 👎 (THUMBS_DOWN) reactions on our finding comments
# ---------------------------------------------------------------------------


def _reaction_thread(body: str, reactors: list[str]) -> dict[str, object]:
    """A review thread whose first comment carries *body* and 👎 *reactors*.

    Matches the shape ``list_downvoted_fingerprints`` selects — the first
    comment's ``body`` plus the users who left a ``THUMBS_DOWN`` reaction.
    """
    return {
        "comments": {
            "nodes": [
                {
                    "body": body,
                    "reactions": {"nodes": [{"user": {"login": login}} for login in reactors]},
                }
            ]
        }
    }


def _mock_permissions(permissions: dict[str, str | None]) -> None:
    """Mock the collaborator-permission endpoint per login (``None`` → 404)."""
    for login, perm in permissions.items():
        resp = (
            httpx.Response(404, json={"message": "Not Found"})
            if perm is None
            else httpx.Response(200, json={"permission": perm})
        )
        respx.route(
            method="GET",
            url=f"{BASE_URL}/repos/{REPO}/collaborators/{login}/permission",
        ).mock(return_value=resp)


@respx.mock
def test_list_downvoted_collects_thumbs_down_finding_fingerprint() -> None:
    """A finding comment an authorised reviewer reacted 👎 to yields its fingerprint."""
    fp = finding_fingerprint("src/app.py", "Import order")
    graphql = _GraphQL(
        threads=[_reaction_thread(f"x <!-- lgtmaybe-finding:{fp} -->", reactors=["maintainer"])]
    )
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)
    _mock_permissions({"maintainer": "write"})

    assert _gateway().list_downvoted_fingerprints() == {fp}


@respx.mock
def test_list_downvoted_ignores_finding_without_thumbs_down() -> None:
    """Our marker but no 👎 → not a suppression signal."""
    fp = finding_fingerprint("src/app.py", "Import order")
    graphql = _GraphQL(threads=[_reaction_thread(f"x <!-- lgtmaybe-finding:{fp} -->", reactors=[])])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    assert _gateway().list_downvoted_fingerprints() == set()


@respx.mock
def test_list_downvoted_ignores_non_lgtmaybe_thread() -> None:
    """A 👎 on a thread that isn't one of ours carries no fingerprint."""
    graphql = _GraphQL(threads=[_reaction_thread("a human comment", reactors=["maintainer"])])
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    assert _gateway().list_downvoted_fingerprints() == set()


@respx.mock
def test_list_downvoted_ignores_unauthorized_reactor() -> None:
    """A 👎 from a user without write access is ignored — on a public repo
    anyone can react, so an unprivileged reaction must never suppress."""
    fp = finding_fingerprint("src/app.py", "Import order")
    graphql = _GraphQL(
        threads=[_reaction_thread(f"x <!-- lgtmaybe-finding:{fp} -->", reactors=["drive_by"])]
    )
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)
    _mock_permissions({"drive_by": "read"})

    assert _gateway().list_downvoted_fingerprints() == set()


@respx.mock
def test_list_downvoted_ignores_non_collaborator_reactor() -> None:
    """A 👎 from a non-collaborator (permission lookup 404s) is ignored — fails closed."""
    fp = finding_fingerprint("src/app.py", "Import order")
    graphql = _GraphQL(
        threads=[_reaction_thread(f"x <!-- lgtmaybe-finding:{fp} -->", reactors=["stranger"])]
    )
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)
    _mock_permissions({"stranger": None})

    assert _gateway().list_downvoted_fingerprints() == set()
