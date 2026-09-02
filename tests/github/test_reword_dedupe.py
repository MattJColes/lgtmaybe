"""Re-run dedupe must survive the model rewording a finding.

The hidden ``<!-- lgtmaybe-finding:… -->`` fingerprint is derived from the
finding's *title* — model prose. The model rewords between runs, so that hash
changes even when the finding is identical in substance, and a dedupe keyed on
it alone lets the same finding post twice.

These tests drive the reword-robust identity (``finding_identity``): what code
was flagged and for what concern, never how it was phrased.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from lgtmaybe.core.findings import finding_fingerprint, finding_identity
from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.github import RestGitHubGateway

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"
HEAD_SHA = "def5678"

BASE_URL = "https://api.github.com"
REVIEWS_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/reviews"
PR_COMMENTS_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/comments"
GRAPHQL_URL = f"{BASE_URL}/graphql"

# src/app.py new-file line 2 ("+import sys") and line 6 ("+    return 0") are
# added lines, so RIGHT-side findings on them anchor to real commentable lines.
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


# Two *identical* added lines ("    return None" at new-file lines 2 and 5), the
# case where an anchor alone cannot tell two findings apart.
DUP_DIFF = """\
diff --git a/src/dup.py b/src/dup.py
index 0000001..0000002 100644
--- a/src/dup.py
+++ b/src/dup.py
@@ -1,1 +1,8 @@
 def a():
+    return None
+
+def b():
+    return None
+
+def c():
+    pass
"""


def _finding(title: str, body: str, *, line: int = 2, anchor: str = "import sys") -> ReviewFinding:
    return ReviewFinding(
        path="src/app.py",
        line=line,
        side="RIGHT",
        severity=Severity.medium,
        title=title,
        body=body,
        anchor=anchor,
        category="correctness",
    )


def _dup_finding(title: str, *, line: int) -> ReviewFinding:
    """A finding on one of ``DUP_DIFF``'s two identical ``return None`` lines."""
    return ReviewFinding(
        path="src/dup.py",
        line=line,
        side="RIGHT",
        severity=Severity.medium,
        title=title,
        body="body",
        anchor="    return None",
        category="correctness",
    )


# The same two findings as the model phrased them on run 1 and, reworded, on
# run 2. Same file, same lines, same flagged source lines, same substance.
RUN_1 = [
    _finding("Import order", "`sys` should be imported before `os`."),
    _finding(
        "Return value unused",
        "`main` returns 0 but no caller checks it.",
        line=6,
        anchor="    return 0",
    ),
]
RUN_2 = [
    _finding("Imports are not sorted", "The `sys` import belongs above `os` here."),
    _finding(
        "main()'s exit code is ignored",
        "Nothing inspects the 0 that `main` hands back.",
        line=6,
        anchor="    return 0",
    ),
]


class _FakePR:
    """A stateful stand-in for one PR's reviews and inline review comments.

    Enough of GitHub to run ``post_review`` twice in a row: the first call finds
    no review and creates one (inline comments ride along in the payload), the
    second finds it, PUTs the body, and posts any *new* inline comments
    individually. ``inline_bodies`` is the full record of what a human would see
    on the PR — the assertion surface for "nothing posted twice".
    """

    def __init__(self) -> None:
        self.review_body: str | None = None
        self.inline_bodies: list[str] = []

    # -- reviews ---------------------------------------------------------
    def list_reviews(self, request: httpx.Request) -> httpx.Response:
        if self.review_body is None:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"id": 99, "body": self.review_body}])

    def create_review(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.review_body = payload["body"]
        for comment in payload.get("comments", []):
            self.inline_bodies.append(comment["body"])
        return httpx.Response(201, json={"id": 99})

    def update_review(self, request: httpx.Request) -> httpx.Response:
        self.review_body = json.loads(request.content)["body"]
        return httpx.Response(200, json={"id": 99})

    # -- inline review comments -----------------------------------------
    def list_comments(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"body": b} for b in self.inline_bodies])

    def create_comment(self, request: httpx.Request) -> httpx.Response:
        self.inline_bodies.append(json.loads(request.content)["body"])
        return httpx.Response(201, json={"id": len(self.inline_bodies)})


def _mount(pr: _FakePR) -> None:
    respx.route(method="GET", url=REVIEWS_URL).mock(side_effect=pr.list_reviews)
    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=pr.create_review)
    respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(side_effect=pr.update_review)
    respx.route(method="GET", url__startswith=PR_COMMENTS_URL).mock(side_effect=pr.list_comments)
    respx.route(method="POST", url=PR_COMMENTS_URL).mock(side_effect=pr.create_comment)
    # No prior threads to auto-resolve — resolve-on-fix is not what's under test.
    respx.route(method="POST", url=GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            },
        )
    )


def _run_diff(findings: list[ReviewFinding], diff: str, summary: str = "Summary text") -> None:
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.mark_reviewed(HEAD_SHA)
    gw.post_review(findings, summary, diff=diff)


def _run(findings: list[ReviewFinding], summary: str) -> None:
    _run_diff(findings, SAMPLE_DIFF, summary)


@respx.mock
def test_rerun_with_reworded_findings_posts_nothing_twice() -> None:
    """Two runs over the same diff, the model rewording every finding on the
    second: each finding must appear inline exactly once."""
    pr = _FakePR()
    _mount(pr)

    _run(RUN_1, "Summary text")
    assert len(pr.inline_bodies) == 2, "first run should post both findings"

    _run(RUN_2, "Summary text")

    assert len(pr.inline_bodies) == 2, (
        "reworded findings were posted again — dedupe is keyed on model prose:\n"
        + "\n---\n".join(pr.inline_bodies)
    )


@respx.mock
def test_rerun_still_posts_a_genuinely_new_finding() -> None:
    """The reword-robust key must not over-suppress: a finding on a line the
    first run never flagged still posts."""
    pr = _FakePR()
    _mount(pr)

    _run([RUN_1[0]], "Summary text")
    _run([RUN_2[0], RUN_1[1]], "Summary text")

    assert len(pr.inline_bodies) == 2
    assert "Return value unused" in pr.inline_bodies[1]


@respx.mock
def test_two_findings_on_identical_source_lines_both_post() -> None:
    """Identical source text in one file and lens is not one finding: both
    occurrences must post, even though they share an identity."""
    pr = _FakePR()
    _mount(pr)

    _run_diff(
        [_dup_finding("first", line=2), _dup_finding("second", line=5)],
        DUP_DIFF,
    )

    assert len(pr.inline_bodies) == 2


@respx.mock
def test_new_occurrence_of_a_duplicated_line_still_posts() -> None:
    """The reword-robust key must not swallow a *genuinely new* finding that
    happens to sit on source text identical to an already-flagged line."""
    pr = _FakePR()
    _mount(pr)

    _run_diff([_dup_finding("first", line=2)], DUP_DIFF)
    # Re-run: the original (reworded) plus a second occurrence the first run missed.
    _run_diff(
        [_dup_finding("first, reworded", line=2), _dup_finding("second", line=5)],
        DUP_DIFF,
    )

    assert len(pr.inline_bodies) == 2, (
        "a new occurrence of a duplicated line was suppressed by its twin's identity"
    )
    assert "second" in pr.inline_bodies[1]


@respx.mock
def test_reworded_duplicate_lines_do_not_multiply() -> None:
    """Both occurrences already posted: a reworded re-run adds nothing."""
    pr = _FakePR()
    _mount(pr)

    _run_diff([_dup_finding("first", line=2), _dup_finding("second", line=5)], DUP_DIFF)
    _run_diff(
        [_dup_finding("1st reworded", line=2), _dup_finding("2nd reworded", line=5)],
        DUP_DIFF,
    )

    assert len(pr.inline_bodies) == 2


def test_finding_identity_ignores_prose() -> None:
    """Identity is (path, category, flagged line) — reword-proof by construction."""
    assert finding_identity(RUN_1[0]) == finding_identity(RUN_2[0])
    assert finding_identity(RUN_1[1]) == finding_identity(RUN_2[1])


def test_finding_identity_separates_distinct_findings() -> None:
    """Different file, lens, side, or flagged code — different id."""
    base = RUN_1[0]
    assert finding_identity(base) != finding_identity(RUN_1[1])
    assert finding_identity(base) != finding_identity(base.model_copy(update={"path": "b.py"}))
    assert finding_identity(base) != finding_identity(
        base.model_copy(update={"category": "security"})
    )
    assert finding_identity(base) != finding_identity(base.model_copy(update={"side": "LEFT"}))
    assert finding_identity(base) != finding_identity(
        base.model_copy(update={"anchor": "import json"})
    )


def test_finding_identity_survives_line_drift() -> None:
    """The model miscounts diff lines; the anchor is the real evidence. Same code
    flagged at a different reported line is the same finding."""
    drifted = RUN_1[0].model_copy(update={"line": 501})
    assert finding_identity(RUN_1[0]) == finding_identity(drifted)


def test_finding_identity_falls_back_to_line_without_an_anchor() -> None:
    """With no anchor there is no code to key on, so the line carries identity —
    still prose-free, just less drift-tolerant."""
    a: Any = RUN_1[0].model_copy(update={"anchor": None})
    b: Any = a.model_copy(update={"title": "totally different words"})
    assert finding_identity(a) == finding_identity(b)
    assert finding_identity(a) != finding_identity(a.model_copy(update={"line": 9}))


# ----------------------------------------------------------------------
# Resolve-on-fix, the other half of the same defect: keyed on prose alone, a
# reworded finding reads as "gone" and its thread gets collapsed — retiring the
# markers that were suppressing the duplicate, so it returns on the next run.
# ----------------------------------------------------------------------


class _ResolveGraphQL:
    """Serves one review thread and records any resolve mutation."""

    def __init__(self, thread_body: str, *, outdated: bool = True) -> None:
        self._thread_body = thread_body
        self._outdated = outdated
        self.resolved: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        query = payload["query"]
        if "reviewThreads" in query:
            node = {
                "id": "THREAD1",
                "isResolved": False,
                "isOutdated": self._outdated,
                "path": "src/app.py",
                "comments": {"nodes": [{"body": self._thread_body, "databaseId": 555}]},
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [node],
                                }
                            }
                        }
                    }
                },
            )
        if "addPullRequestReviewThreadReply" in query:
            return httpx.Response(200, json={"data": {"addPullRequestReviewThreadReply": {}}})
        if "resolveReviewThread" in query:
            self.resolved.append(payload["variables"]["threadId"])
            return httpx.Response(200, json={"data": {"resolveReviewThread": {}}})
        raise AssertionError(f"unexpected GraphQL operation: {query}")


def _existing_review() -> None:
    """A prior lgtmaybe review exists, so this run takes the re-run path."""
    respx.route(method="GET", url=REVIEWS_URL).mock(
        return_value=httpx.Response(200, json=[{"id": 99, "body": "Old summary <!-- lgtmaybe -->"}])
    )
    respx.route(method="PUT", url=f"{REVIEWS_URL}/99").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )


@respx.mock
def test_reworded_finding_does_not_resolve_its_own_open_thread() -> None:
    """A thread whose finding this run still reports — in different words — is
    not "fixed", even though GitHub marks it outdated."""
    _existing_review()
    respx.route(method="GET", url__startswith=PR_COMMENTS_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.route(method="POST", url=PR_COMMENTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    respx.route(method="PATCH").mock(return_value=httpx.Response(200, json={}))
    # The thread run 1 opened, carrying both of run 1's hidden ids.
    thread_body = (
        "**[MEDIUM] Import order**\n\nbody\n\n"
        f"<!-- lgtmaybe-finding:{finding_fingerprint('src/app.py', 'Import order')} -->\n"
        f"<!-- lgtmaybe-identity:{finding_identity(RUN_1[0])} -->"
    )
    graphql = _ResolveGraphQL(thread_body)
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    _run([RUN_2[0]], "New summary")  # the same finding, reworded

    assert graphql.resolved == [], "resolved a thread whose finding is still being reported"


@respx.mock
def test_genuinely_fixed_thread_is_still_resolved() -> None:
    """The guard above must not freeze resolve-on-fix: a thread whose finding
    this run does not report at all still collapses."""
    _existing_review()
    respx.route(method="GET", url__startswith=PR_COMMENTS_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.route(method="POST", url=PR_COMMENTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    # Capture the marker rewrite rather than asserting inside the mock: resolve-on-fix
    # is best-effort and swallows per-thread exceptions, so an assertion raised in
    # here would be logged away and the test would pass regardless.
    patched: list[str] = []

    def capture_patch(request: httpx.Request) -> httpx.Response:
        patched.append(json.loads(request.content)["body"])
        return httpx.Response(200, json={})

    respx.route(method="PATCH").mock(side_effect=capture_patch)
    gone = _finding("Removed bug", "body", line=6, anchor="    return 0")
    thread_body = (
        "**[MEDIUM] Removed bug**\n\nbody\n\n"
        f"<!-- lgtmaybe-finding:{finding_fingerprint('src/app.py', 'Removed bug')} -->\n"
        f"<!-- lgtmaybe-identity:{finding_identity(gone)} -->"
    )
    graphql = _ResolveGraphQL(thread_body)
    respx.route(method="POST", url=GRAPHQL_URL).mock(side_effect=graphql)

    _run([RUN_2[0]], "New summary")  # a different finding entirely

    assert graphql.resolved == ["THREAD1"]
    # Both marker families must be retired together. Leaving the identity marker
    # active would keep suppressing this finding forever if it were reintroduced —
    # exactly the trap the fingerprint rewrite already exists to avoid.
    assert len(patched) == 1
    assert "<!-- lgtmaybe-resolved-fingerprint:" in patched[0]
    assert "<!-- lgtmaybe-resolved-identity:" in patched[0]
    assert "<!-- lgtmaybe-finding:" not in patched[0]
    assert "<!-- lgtmaybe-identity:" not in patched[0]
