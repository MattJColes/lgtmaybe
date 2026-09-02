"""Tests for GiteaGateway — respx-mocked Gitea REST API.

Gitea's API mirrors GitHub's closely enough that the interesting cases are the
places it *doesn't*: reviews are not editable, so the summary lives in an
upserted issue comment; inline comments are positioned by ``new_position`` /
``old_position`` rather than ``line`` + ``side``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.gitea import GiteaGateway

HOST = "gitea.example.com"
REPO = "owner/repo"
PR_NUMBER = 7
TOKEN = "gitea_test_token"

API = f"https://{HOST}/api/v1/repos/{REPO}"
PR_URL = f"{API}/pulls/{PR_NUMBER}"

DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
+password = "hunter2"
 
 def main():
"""

PR_DETAIL = {
    "base": {"sha": "base123", "ref": "main"},
    "head": {"sha": "head456", "ref": "feature-branch"},
    "title": "Add a thing",
    "body": "This adds the thing.",
}


def _gateway(client: httpx.Client) -> GiteaGateway:
    return GiteaGateway(host=HOST, repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)


def _stub_context_routes() -> None:
    """The read routes get_pr_context needs, in first-match order."""
    respx.get(f"{PR_URL}.diff").mock(return_value=httpx.Response(200, text=DIFF))
    respx.get(PR_URL).mock(return_value=httpx.Response(200, json=PR_DETAIL))
    respx.route(method="GET", url__startswith=f"{PR_URL}/files").mock(
        return_value=httpx.Response(200, json=[{"filename": "app.py"}])
    )
    respx.route(method="GET", url__startswith=f"{PR_URL}/commits").mock(
        return_value=httpx.Response(200, json=[{"commit": {"message": "add a thing\n\nbody"}}])
    )
    respx.route(method="GET", url__startswith=f"{API}/contents/").mock(
        return_value=httpx.Response(200, json={"content": "aW1wb3J0IG9z", "encoding": "base64"})
    )


class TestGetPRContext:
    @respx.mock
    def test_returns_the_diff_and_both_shas(self) -> None:
        _stub_context_routes()
        ctx = _gateway(httpx.Client()).get_pr_context()

        assert ctx.diff == DIFF
        assert ctx.base_sha == "base123"
        assert ctx.head_sha == "head456"
        assert ctx.changed_files == ["app.py"]

    @respx.mock
    def test_carries_the_stated_intent_for_the_intent_lens(self) -> None:
        _stub_context_routes()
        ctx = _gateway(httpx.Client()).get_pr_context()

        assert ctx.title == "Add a thing"
        assert ctx.description == "This adds the thing."
        assert ctx.commit_messages == ["add a thing"], "only the subject line, not the body"
        assert ctx.head_branch == "feature-branch"

    @respx.mock
    def test_strips_leading_whitespace_from_commit_subjects(self) -> None:
        _stub_context_routes()
        respx.route(method="GET", url__startswith=f"{PR_URL}/commits").mock(
            return_value=httpx.Response(200, json=[{"commit": {"message": "\nfix things"}}])
        )

        ctx = _gateway(httpx.Client()).get_pr_context()

        assert ctx.commit_messages == ["fix things"]

    @respx.mock
    def test_decodes_base64_file_contents_for_hunk_expansion(self) -> None:
        _stub_context_routes()
        ctx = _gateway(httpx.Client()).get_pr_context()

        assert ctx.file_contents == {"app.py": "import os"}

    @respx.mock
    def test_a_missing_sha_fails_loudly_rather_than_reviewing_nothing(self) -> None:
        respx.get(f"{PR_URL}.diff").mock(return_value=httpx.Response(200, text=DIFF))
        respx.get(PR_URL).mock(return_value=httpx.Response(200, json={"title": "no shas"}))

        with pytest.raises(RuntimeError, match="base/head SHA"):
            _gateway(httpx.Client()).get_pr_context()


class TestPostReview:
    @respx.mock
    def test_anchors_an_inline_comment_by_new_position(self) -> None:
        """Gitea positions a comment by new/old file line, not GitHub's line+side."""
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))
        respx.route(method="GET", url__startswith=f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )

        finding = ReviewFinding(
            path="app.py",
            line=2,
            side="RIGHT",
            severity=Severity.high,
            title="Hardcoded password",
            body="Move it to the environment.",
            anchor='password = "hunter2"',
            anchored=True,
        )
        _gateway(httpx.Client()).post_review([finding], "1 finding", diff=DIFF)

        assert reviews.called
        posted = reviews.calls[0].request
        import json as _json

        comment = _json.loads(posted.content)["comments"][0]
        assert comment["path"] == "app.py"
        assert comment["new_position"] == 2
        assert "old_position" not in comment
        assert "Hardcoded password" in comment["body"]

    @respx.mock
    def test_a_left_side_finding_uses_old_position(self) -> None:
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))
        respx.route(method="GET", url__startswith=f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )

        finding = ReviewFinding(
            path="app.py",
            line=1,
            side="LEFT",
            severity=Severity.low,
            title="Removed import",
            body="Was this deliberate?",
            anchor="import os",
            anchored=True,
        )
        _gateway(httpx.Client()).post_review([finding], "1 finding", diff=DIFF)

        import json as _json

        comment = _json.loads(reviews.calls[0].request.content)["comments"][0]
        assert comment["old_position"] == 1
        assert "new_position" not in comment

    @respx.mock
    def test_the_summary_is_upserted_so_a_rerun_edits_it_in_place(self) -> None:
        """Gitea reviews cannot be edited, so the summary lives in an issue comment."""
        respx.route(method="GET", url__startswith=f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        listed = respx.route(
            method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments"
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    *[{"id": i, "body": "human comment"} for i in range(49)],
                    {"id": 99, "body": "old summary\n\n<!-- lgtmaybe -->"},
                ],
            )
        )
        edit = respx.patch(f"{API}/issues/comments/99").mock(
            return_value=httpx.Response(200, json={"id": 99})
        )
        create = respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 100})
        )

        _gateway(httpx.Client()).post_review([], "👍 LGTM!", diff=DIFF)

        assert edit.called, "an existing summary must be edited"
        assert not create.called, "and not duplicated"
        assert listed.call_count == 1

    @respx.mock
    def test_a_finding_already_posted_is_not_posted_twice(self) -> None:
        """Reviews are immutable here, so dedupe has to happen before posting."""
        from lgtmaybe.core.findings import finding_fingerprint

        finding = ReviewFinding(
            path="app.py",
            line=2,
            side="RIGHT",
            severity=Severity.high,
            title="Hardcoded password",
            body="Move it to the environment.",
            anchor='password = "hunter2"',
            anchored=True,
        )
        already = finding_fingerprint("app.py", "Hardcoded password")
        respx.route(method="GET", url=f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[{"id": 5}])
        )
        listed = respx.get(f"{PR_URL}/reviews/5/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    *[{"body": "human comment"} for _ in range(49)],
                    {"body": f"old text\n<!-- lgtmaybe-finding:{already} -->"},
                ],
            )
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        _gateway(httpx.Client()).post_review([finding], "1 finding", diff=DIFF)

        assert not reviews.called, "the only finding was already on the PR"
        assert listed.call_count == 1

    @respx.mock
    def test_dedupe_consumes_each_existing_finding_once(self) -> None:
        from lgtmaybe.core.findings import finding_fingerprint

        already = finding_fingerprint("app.py", "Hardcoded password")
        respx.get(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json=[{"id": 5}]))
        respx.get(f"{PR_URL}/reviews/5/comments").mock(
            return_value=httpx.Response(
                200, json=[{"body": f"old\n<!-- lgtmaybe-finding:{already} -->"}]
            )
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={})
        )
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))
        duplicate_diff = DIFF.replace(
            '+password = "hunter2"', '+password = "hunter2"\n+password = "hunter2"'
        )
        findings = [
            ReviewFinding(
                path="app.py",
                line=line,
                side="RIGHT",
                severity=Severity.high,
                title="Hardcoded password",
                body="Move it to the environment.",
                anchor='password = "hunter2"',
                anchored=True,
            )
            for line in (2, 3)
        ]

        _gateway(httpx.Client()).post_review(findings, "2 findings", diff=duplicate_diff)

        assert reviews.call_count == 1
        assert len(json.loads(reviews.calls[0].request.content)["comments"]) == 1

    @respx.mock
    def test_dedupe_reads_every_past_review_not_just_the_first(self) -> None:
        """Each run leaves its own immutable review, so all of them must be read."""
        from lgtmaybe.core.findings import finding_fingerprint

        finding = ReviewFinding(
            path="app.py",
            line=2,
            side="RIGHT",
            severity=Severity.high,
            title="Hardcoded password",
            body="Move it to the environment.",
            anchor='password = "hunter2"',
            anchored=True,
        )
        already = finding_fingerprint("app.py", "Hardcoded password")
        respx.route(method="GET", url=f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[{"id": 5}, {"id": 6}, {"id": 7}])
        )
        for review_id in (5, 6):
            respx.get(f"{PR_URL}/reviews/{review_id}/comments").mock(
                return_value=httpx.Response(200, json=[{"body": "unrelated"}])
            )
        # Only the LAST review carries the finding — a walk that stopped early,
        # or lost a concurrent result, would re-post it.
        respx.get(f"{PR_URL}/reviews/7/comments").mock(
            return_value=httpx.Response(
                200, json=[{"body": f"old text\n<!-- lgtmaybe-finding:{already} -->"}]
            )
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        _gateway(httpx.Client()).post_review([finding], "1 finding", diff=DIFF)

        assert not reviews.called, "the only finding was already on the PR"

    @respx.mock
    def test_an_unanchorable_finding_is_demoted_into_the_summary(self) -> None:
        """A comment on a line we cannot stand behind is worse than no line at all."""
        respx.route(method="GET", url__startswith=f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        create = respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        finding = ReviewFinding(
            path="app.py",
            line=999,
            side="RIGHT",
            severity=Severity.medium,
            title="Unplaceable",
            body="Could not be anchored.",
            anchored=False,
        )
        _gateway(httpx.Client()).post_review([finding], "1 finding", diff=DIFF)

        import json as _json

        body = _json.loads(create.calls[0].request.content)["body"]
        assert "Additional findings" in body
        assert "Unplaceable" in body
        assert not reviews.called


class TestCapabilities:
    """What this adapter can and cannot do, declared rather than discovered."""

    def test_declares_the_capabilities_it_implements(self) -> None:
        from lgtmaybe.core import ports

        gateway = GiteaGateway.__new__(GiteaGateway)
        for name in (
            "SupportsFileContents",
            "SupportsDescribe",
            "SupportsDiagram",
            "SupportsLabels",
            "SupportsChecks",
        ):
            assert isinstance(gateway, getattr(ports, name)), name

    def test_does_not_claim_what_gitea_cannot_do(self) -> None:
        """Gitea has no resolvable review threads; claiming it would break the caller."""
        from lgtmaybe.core import ports

        gateway = GiteaGateway.__new__(GiteaGateway)
        assert not isinstance(gateway, ports.SupportsThreadResolution)


class TestLabels:
    @respx.mock
    def test_resolves_label_names_to_ids_before_applying(self) -> None:
        """Gitea addresses labels by id, so names have to be looked up first."""
        respx.route(method="GET", url__startswith=f"{API}/labels").mock(
            return_value=httpx.Response(
                200, json=[{"id": 3, "name": "review-effort/2"}, {"id": 4, "name": "other"}]
            )
        )
        applied = respx.post(f"{API}/issues/{PR_NUMBER}/labels").mock(
            return_value=httpx.Response(200, json=[])
        )

        _gateway(httpx.Client()).apply_pr_labels(["review-effort/2"])

        import json as _json

        assert _json.loads(applied.calls[0].request.content)["labels"] == [3]

    @respx.mock
    def test_a_label_the_repo_does_not_have_is_skipped_not_created(self) -> None:
        """Creating labels on someone's repo is not this tool's business."""
        respx.route(method="GET", url__startswith=f"{API}/labels").mock(
            return_value=httpx.Response(200, json=[{"id": 3, "name": "known"}])
        )
        applied = respx.post(f"{API}/issues/{PR_NUMBER}/labels").mock(
            return_value=httpx.Response(200, json=[])
        )

        _gateway(httpx.Client()).apply_pr_labels(["never-heard-of-it"])

        assert not applied.called

    @respx.mock
    def test_a_label_failure_never_fails_the_review(self) -> None:
        respx.route(method="GET", url__startswith=f"{API}/labels").mock(
            return_value=httpx.Response(500)
        )
        _gateway(httpx.Client()).apply_pr_labels(["anything"])  # must not raise

    def test_no_labels_makes_no_request(self) -> None:
        with respx.mock:
            _gateway(httpx.Client()).apply_pr_labels([])
            assert not respx.calls


class TestCheckRun:
    @respx.mock
    @pytest.mark.parametrize(
        ("conclusion", "state"),
        [("success", "success"), ("failure", "failure")],
    )
    def test_maps_a_conclusion_onto_giteas_narrower_state_vocabulary(
        self, conclusion: str, state: str
    ) -> None:
        posted = respx.post(f"{API}/statuses/head456").mock(
            return_value=httpx.Response(201, json={})
        )
        _gateway(httpx.Client()).create_check_run("head456", conclusion, "lgtmaybe", "summary")

        import json as _json

        payload = _json.loads(posted.calls[0].request.content)
        assert payload["state"] == state
        assert payload["context"] == "lgtmaybe"

    @respx.mock
    def test_an_unknown_conclusion_does_not_fail_the_review(self) -> None:
        posted = respx.post(f"{API}/statuses/head456").mock(
            return_value=httpx.Response(201, json={})
        )
        _gateway(httpx.Client()).create_check_run("head456", "brand-new", "lgtmaybe", "summary")

        import json as _json

        assert _json.loads(posted.calls[0].request.content)["state"] == "success"

    @respx.mock
    def test_a_status_failure_never_fails_the_review(self) -> None:
        respx.post(f"{API}/statuses/head456").mock(return_value=httpx.Response(500))
        _gateway(httpx.Client()).create_check_run("head456", "success", "t", "s")  # must not raise


class TestFileContentEscaping:
    @respx.mock
    def test_a_path_with_a_fragment_character_still_reaches_the_server(self) -> None:
        """An unescaped `#` opens a URL fragment: the path truncates and the
        `ref` query is swallowed into the discarded remainder."""
        captured: list[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"content": "aW1wb3J0IG9z", "encoding": "base64"})

        respx.route(method="GET", url__startswith=f"{API}/contents/").mock(side_effect=_record)

        assert (
            _gateway(httpx.Client())._get_file_content("src/C#/Foo.cs", "deadbeef") == "import os"
        )
        # `.path` decodes; the wire form is what the `#` breaks.
        assert captured[0].url.raw_path.endswith(b"/contents/src/C%23/Foo.cs?ref=deadbeef")
        assert captured[0].url.params["ref"] == "deadbeef"
