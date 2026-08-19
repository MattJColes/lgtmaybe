"""Tests for GitLabGateway — respx-mocked GitLab REST API.

GitLab is the forge that differs most: merge requests, not pull requests;
discussions positioned by old/new path and line rather than line + side; and no
batched review object at all, so each finding is its own discussion.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.gitlab import GitLabGateway

HOST = "gitlab.example.com"
REPO = "group/sub/project"
MR_IID = 7
TOKEN = "glpat-test"

# GitLab addresses a project by its URL-encoded path.
PROJECT = "group%2Fsub%2Fproject"
API = f"https://{HOST}/api/v4/projects/{PROJECT}"
MR_URL = f"{API}/merge_requests/{MR_IID}"

DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
+password = "hunter2"
 
 def main():
"""

MR_DETAIL = {
    "title": "Add a thing",
    "description": "This adds the thing.",
    "source_branch": "feature-branch",
    "diff_refs": {"base_sha": "base123", "head_sha": "head456", "start_sha": "start789"},
}

FINDING = ReviewFinding(
    path="app.py",
    line=2,
    side="RIGHT",
    severity=Severity.high,
    title="Hardcoded password",
    body="Move it to the environment.",
    anchor='password = "hunter2"',
    anchored=True,
)


def _gateway(client: httpx.Client | None = None) -> GitLabGateway:
    return GitLabGateway(
        host=HOST,
        repo=REPO,
        pr_number=MR_IID,
        token=TOKEN,
        client=client if client is not None else httpx.Client(),
    )


def _stub_context_routes() -> None:
    respx.get(f"{MR_URL}/raw_diffs").mock(return_value=httpx.Response(200, text=DIFF))
    respx.get(MR_URL).mock(return_value=httpx.Response(200, json=MR_DETAIL))
    respx.route(method="GET", url__startswith=f"{MR_URL}/diffs").mock(
        return_value=httpx.Response(200, json=[{"new_path": "app.py", "old_path": "app.py"}])
    )
    respx.route(method="GET", url__startswith=f"{MR_URL}/commits").mock(
        return_value=httpx.Response(200, json=[{"title": "add a thing"}])
    )
    respx.route(method="GET", url__startswith=f"{API}/repository/files/").mock(
        return_value=httpx.Response(200, json={"content": "aW1wb3J0IG9z", "encoding": "base64"})
    )


def _stub_post_routes() -> None:
    respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={"id": 1}))


class TestProjectAddressing:
    def test_the_project_path_is_url_encoded(self) -> None:
        """GitLab groups nest, so the path needs encoding to survive the URL."""
        assert _gateway()._project == PROJECT

    def test_self_hosted_hosts_are_carried_through(self) -> None:
        assert _gateway()._api.startswith(f"https://{HOST}/api/v4/")


class TestGetPRContext:
    @respx.mock
    def test_returns_the_diff_and_both_shas(self) -> None:
        _stub_context_routes()
        ctx = _gateway().get_pr_context()

        assert ctx.diff == DIFF
        assert ctx.base_sha == "base123"
        assert ctx.head_sha == "head456"
        assert ctx.changed_files == ["app.py"]

    @respx.mock
    def test_carries_the_stated_intent_for_the_intent_lens(self) -> None:
        _stub_context_routes()
        ctx = _gateway().get_pr_context()

        assert ctx.title == "Add a thing"
        assert ctx.description == "This adds the thing."
        assert ctx.commit_messages == ["add a thing"]
        assert ctx.head_branch == "feature-branch"

    @respx.mock
    def test_decodes_base64_file_contents_for_hunk_expansion(self) -> None:
        _stub_context_routes()
        assert _gateway().get_pr_context().file_contents == {"app.py": "import os"}

    @respx.mock
    def test_missing_diff_refs_fail_loudly(self) -> None:
        respx.get(f"{MR_URL}/raw_diffs").mock(return_value=httpx.Response(200, text=DIFF))
        respx.get(MR_URL).mock(return_value=httpx.Response(200, json={"title": "no refs"}))

        with pytest.raises(RuntimeError, match="base/head SHA"):
            _gateway().get_pr_context()


class TestPostReview:
    @respx.mock
    def test_each_finding_becomes_its_own_positioned_discussion(self) -> None:
        """GitLab has no batched review object — a discussion per finding."""
        _stub_post_routes()
        created = respx.post(f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(201, json={"id": "abc"})
        )
        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.post_review([FINDING], "1 finding", diff=DIFF)

        assert created.called
        payload = json.loads(created.calls[0].request.content)
        assert payload["position"]["new_path"] == "app.py"
        assert payload["position"]["new_line"] == 2
        assert payload["position"]["position_type"] == "text"
        assert payload["position"]["base_sha"] == "base123"
        assert payload["position"]["head_sha"] == "head456"
        assert payload["position"]["start_sha"] == "start789"
        assert "Hardcoded password" in payload["body"]

    @respx.mock
    def test_a_left_side_finding_positions_on_the_old_line(self) -> None:
        _stub_post_routes()
        created = respx.post(f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(201, json={"id": "abc"})
        )
        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.post_review(
            [FINDING.model_copy(update={"side": "LEFT", "line": 1, "anchor": "import os"})],
            "1 finding",
            diff=DIFF,
        )

        position = json.loads(created.calls[0].request.content)["position"]
        assert position["old_line"] == 1
        assert "new_line" not in position

    @respx.mock
    def test_the_summary_note_is_upserted_so_a_rerun_edits_it(self) -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(
                200, json=[{"id": 55, "body": "old summary\n\n<!-- lgtmaybe -->"}]
            )
        )
        edit = respx.put(f"{MR_URL}/notes/55").mock(return_value=httpx.Response(200, json={}))
        create = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))

        _gateway().post_review([], "👍 LGTM!", diff=DIFF)

        assert edit.called
        assert not create.called

    @respx.mock
    def test_a_finding_already_discussed_is_not_posted_again(self) -> None:
        from lgtmaybe.core.findings import finding_fingerprint

        already = finding_fingerprint("app.py", "Hardcoded password")
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "d1",
                        "notes": [
                            {
                                "id": 9,
                                "body": f"old\n<!-- lgtmaybe-finding:{already} -->",
                                "resolved": False,
                                "position": {"new_path": "app.py"},
                            }
                        ],
                    }
                ],
            )
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))
        created = respx.post(f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(201, json={})
        )

        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.post_review([FINDING], "1 finding", diff=DIFF)

        assert not created.called

    @respx.mock
    def test_an_unanchorable_finding_is_demoted_into_the_summary(self) -> None:
        _stub_post_routes()
        create = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))
        created = respx.post(f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(201, json={})
        )

        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.post_review(
            [FINDING.model_copy(update={"line": 999, "anchored": False})], "1 finding", diff=DIFF
        )

        body = json.loads(create.calls[0].request.content)["body"]
        assert "Additional findings" in body
        assert not created.called


class TestThreadResolution:
    """GitLab discussions resolve over plain REST — no GraphQL, unlike GitHub."""

    DISCUSSIONS = [
        {
            "id": "thread-1",
            "notes": [
                {
                    "id": 11,
                    "body": (
                        "a finding\n"
                        "<!-- lgtmaybe-finding:aaaaaaaaaaaa -->\n"
                        "<!-- lgtmaybe-identity:bbbbbbbbbbbb -->"
                    ),
                    "resolved": False,
                    "resolvable": True,
                    "position": {"new_path": "app.py"},
                }
            ],
        },
        {
            "id": "thread-2",
            "notes": [
                {
                    "id": 22,
                    "body": "a human comment",
                    "resolved": False,
                    "resolvable": True,
                    "position": {"new_path": "app.py"},
                }
            ],
        },
    ]

    @respx.mock
    def test_lists_only_our_own_unresolved_findings(self) -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=self.DISCUSSIONS)
        )
        active = _gateway().list_active_findings()

        assert [f.thread_id for f in active] == ["thread-1"], "a human's thread is not ours"
        assert active[0].fingerprint == "aaaaaaaaaaaa"
        assert active[0].identity == "bbbbbbbbbbbb", "both hidden id families are read back"
        assert active[0].path == "app.py"

    @respx.mock
    def test_a_resolved_thread_is_not_active(self) -> None:
        resolved = [{**self.DISCUSSIONS[0]}]
        resolved[0]["notes"] = [{**resolved[0]["notes"][0], "resolved": True}]
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=resolved)
        )
        assert _gateway().list_active_findings() == []

    @respx.mock
    def test_only_validated_threads_are_replied_to_and_resolved(self) -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=self.DISCUSSIONS)
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))
        reply = respx.post(f"{MR_URL}/discussions/thread-1/notes").mock(
            return_value=httpx.Response(201, json={})
        )
        resolve = respx.put(f"{MR_URL}/discussions/thread-1").mock(
            return_value=httpx.Response(200, json={})
        )
        other = respx.put(f"{MR_URL}/discussions/thread-2").mock(
            return_value=httpx.Response(200, json={})
        )

        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.list_active_findings()
        gateway.set_validated_fixed_threads({"thread-1"})
        gateway.post_review([], "👍 LGTM!", diff=DIFF)

        assert reply.called, "a resolved finding is explained before it is closed"
        assert resolve.called
        assert not other.called, "a thread nobody validated stays open"

    @respx.mock
    def test_an_empty_allowlist_resolves_nothing(self) -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=self.DISCUSSIONS)
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))
        resolve = respx.put(f"{MR_URL}/discussions/thread-1").mock(
            return_value=httpx.Response(200, json={})
        )

        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.list_active_findings()
        gateway.set_validated_fixed_threads(set())
        gateway.post_review([], "👍 LGTM!", diff=DIFF)

        assert not resolve.called

    @respx.mock
    def test_counts_open_finding_threads_for_the_disclosure_line(self) -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=self.DISCUSSIONS)
        )
        assert _gateway().count_open_finding_threads() == 1


class TestCapabilities:
    def test_declares_what_gitlab_can_serve(self) -> None:
        from lgtmaybe.core import ports

        gateway = GitLabGateway.__new__(GitLabGateway)
        for name in (
            "SupportsFileContents",
            "SupportsDescribe",
            "SupportsDiagram",
            "SupportsLabels",
            "SupportsChecks",
            "SupportsThreadResolution",
        ):
            assert isinstance(gateway, getattr(ports, name)), name

    def test_does_not_claim_incremental_review(self) -> None:
        """Reviewing an increment needs a diff between two commits; that comes later."""
        from lgtmaybe.core import ports

        assert not isinstance(GitLabGateway.__new__(GitLabGateway), ports.SupportsIncremental)


class TestLabels:
    @respx.mock
    def test_labels_are_added_in_one_update(self) -> None:
        """GitLab takes labels as a comma-separated field on the MR itself."""
        updated = respx.put(MR_URL).mock(return_value=httpx.Response(200, json={}))
        _gateway().apply_pr_labels(["review-effort/2", "possible-security-issue"])

        payload = json.loads(updated.calls[0].request.content)
        assert payload["add_labels"] == "review-effort/2,possible-security-issue"

    @respx.mock
    def test_a_label_failure_never_fails_the_review(self) -> None:
        respx.put(MR_URL).mock(return_value=httpx.Response(500))
        _gateway().apply_pr_labels(["anything"])  # must not raise

    def test_no_labels_makes_no_request(self) -> None:
        with respx.mock:
            _gateway().apply_pr_labels([])
            assert not respx.calls


class TestCheckRun:
    @respx.mock
    @pytest.mark.parametrize(
        ("conclusion", "state"),
        [("success", "success"), ("failure", "failed"), ("cancelled", "canceled")],
    )
    def test_maps_a_conclusion_onto_gitlabs_state_vocabulary(
        self, conclusion: str, state: str
    ) -> None:
        posted = respx.post(f"{API}/statuses/head456").mock(
            return_value=httpx.Response(201, json={})
        )
        _gateway().create_check_run("head456", conclusion, "lgtmaybe", "summary")

        assert json.loads(posted.calls[0].request.content)["state"] == state

    @respx.mock
    def test_a_status_failure_never_fails_the_review(self) -> None:
        respx.post(f"{API}/statuses/head456").mock(return_value=httpx.Response(500))
        _gateway().create_check_run("head456", "success", "t", "s")  # must not raise


class TestResilience:
    @respx.mock
    def test_a_rejected_position_does_not_take_the_review_down(self) -> None:
        """GitLab 400s a position whose line no longer exists in the current diff."""
        _stub_post_routes()
        respx.post(f"{MR_URL}/discussions").mock(return_value=httpx.Response(400))
        note = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))

        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.post_review([FINDING], "1 finding", diff=DIFF)  # must not raise

        assert note.called, "the summary still posts"

    @respx.mock
    def test_findings_are_demoted_when_the_diff_refs_are_unknown(self) -> None:
        """A position without the three SHAs is rejected, so do not build one."""
        _stub_post_routes()
        created = respx.post(f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(201, json={})
        )
        note = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))

        _gateway().post_review([FINDING], "1 finding", diff=DIFF)

        assert not created.called
        assert "Hardcoded password" in json.loads(note.calls[0].request.content)["body"]
