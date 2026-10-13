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
# The user conftest's identity fixture answers for `GET /api/v4/user`; GitLab
# ownership is decided by user id.
BOT = "lgtmaybe-bot"
BOT_ID = 99

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


class TestDiscussionFetching:
    """The discussion list is read three times a run — over one fetch."""

    @respx.mock
    def test_the_discussion_list_is_fetched_once_across_context_and_post(self) -> None:
        _stub_context_routes()
        _stub_post_routes()
        listed = respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=[])
        )
        gateway = _gateway()

        gateway.get_pr_context()  # counts open finding threads
        gateway.post_review([FINDING], "summary")  # reads them again to de-dupe

        assert listed.call_count == 1


class TestPostReview:
    @respx.mock
    def test_renamed_file_uses_its_real_old_path(self) -> None:
        renamed_diff = DIFF.replace("a/app.py b/app.py", "a/old.py b/app.py").replace(
            "--- a/app.py", "--- a/old.py"
        )
        _stub_context_routes()
        _stub_post_routes()
        respx.get(f"{MR_URL}/raw_diffs").mock(return_value=httpx.Response(200, text=renamed_diff))
        respx.route(method="GET", url__startswith=f"{MR_URL}/diffs").mock(
            return_value=httpx.Response(200, json=[{"new_path": "app.py", "old_path": "old.py"}])
        )
        created = respx.post(f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(201, json={"id": "abc"})
        )
        gateway = _gateway()

        ctx = gateway.get_pr_context()
        gateway.post_review([FINDING], "1 finding", diff=ctx.diff)

        position = json.loads(created.calls[0].request.content)["position"]
        assert position["old_path"] == "old.py"
        assert position["new_path"] == "app.py"

    @respx.mock
    def test_note_families_share_one_notes_fetch(self) -> None:
        _stub_post_routes()
        listed = respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(200, json=[])
        )
        gateway = _gateway()

        gateway.post_review([], "summary", diff=DIFF)
        gateway.post_describe_comment("description")
        gateway.post_diagram_comment("diagram")

        assert listed.call_count == 1

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
    def test_a_context_finding_positions_on_both_lines(self) -> None:
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
        assert position["new_line"] == 1

    @respx.mock
    def test_the_summary_note_is_upserted_so_a_rerun_edits_it(self) -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 55, "body": "old summary\n\n<!-- lgtmaybe -->", "author": {"id": 99}}],
            )
        )
        respx.get(f"https://{HOST}/api/v4/user").mock(
            return_value=httpx.Response(200, json={"id": 99})
        )
        edit = respx.put(f"{MR_URL}/notes/55").mock(return_value=httpx.Response(200, json={}))
        create = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))

        _gateway().post_review([], "👍 LGTM!", diff=DIFF)

        assert edit.called
        assert not create.called

    @respx.mock
    def test_the_summary_note_keeps_the_risk_marker(self) -> None:
        """The engine stamps the risk verdict; the adapter must post it verbatim."""
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(200, json=[])
        )
        create = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))

        _gateway().post_review([], "0 findings · risk high\n<!-- lgtmaybe-risk:high -->", diff=DIFF)

        assert "<!-- lgtmaybe-risk:high -->" in json.loads(create.calls[0].request.content)["body"]

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
                                "author": {"id": BOT_ID},
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
    def test_dedupe_consumes_each_existing_finding_once(self) -> None:
        from lgtmaybe.core.findings import finding_fingerprint

        already = finding_fingerprint("app.py", "Hardcoded password")
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "notes": [
                            {
                                "body": f"old\n<!-- lgtmaybe-finding:{already} -->",
                                "resolved": False,
                                "author": {"id": BOT_ID},
                            }
                        ]
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
        duplicate_diff = DIFF.replace(
            '+password = "hunter2"', '+password = "hunter2"\n+password = "hunter2"'
        )
        findings = [FINDING, FINDING.model_copy(update={"line": 3})]
        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]

        gateway.post_review(findings, "2 findings", diff=duplicate_diff)

        assert created.call_count == 1

    @respx.mock
    def test_a_resolved_finding_may_be_posted_again(self) -> None:
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
                                "resolved": True,
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

        assert created.called

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
                    "author": {"id": BOT_ID},
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


def _whoami(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list[httpx.Request]:
    """Answer `GET /api/v4/user` with *response*, recording each lookup."""
    original = httpx.Client.send
    lookups: list[httpx.Request] = []

    def send(client: httpx.Client, request: httpx.Request, **kwargs: object) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v4/user":
            lookups.append(request)
            response.request = request
            return response
        return original(client, request, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.Client, "send", send)
    return lookups


def _discussion(thread_id: str, body: str, author: str) -> dict:
    return {
        "id": thread_id,
        "notes": [
            {
                "id": 1,
                "body": body,
                "resolved": False,
                "position": {"new_path": "app.py"},
                "author": {"id": BOT_ID if author == BOT else 4321},
            }
        ],
    }


class TestAuthorship:
    """Hidden ids and markers count only on notes the posting identity wrote.

    The identity id is a prose-free hash of path, lens and anchor, so an MR
    author can compute it, post it in a discussion of their own, and then push
    the line it describes — the finding would never be posted.
    """

    @respx.mock
    def test_a_forged_id_in_someone_elses_discussion_does_not_hide_a_finding(self) -> None:
        from lgtmaybe.core.findings import finding_identity

        forged = f"nice <!-- lgtmaybe-identity:{finding_identity(FINDING)} -->"
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=[_discussion("d1", forged, "mr-author")])
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

        assert created.called, "a stranger's copy of our id must not suppress the finding"

    @respx.mock
    def test_someone_elses_discussion_is_neither_active_nor_counted(self) -> None:
        body = "<!-- lgtmaybe-finding:aaaaaaaaaaaa -->"
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(
                200,
                json=[_discussion("theirs", body, "mr-author"), _discussion("ours", body, BOT)],
            )
        )
        gateway = _gateway()

        assert [f.thread_id for f in gateway.list_active_findings()] == ["ours"]
        assert gateway.count_open_finding_threads() == 1

    @respx.mock
    def test_someone_elses_note_carrying_our_marker_is_never_edited(self) -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 99, "body": "lol <!-- lgtmaybe -->", "author": {"id": 4321}},
                    {"id": 55, "body": "old\n\n<!-- lgtmaybe -->", "author": {"id": BOT_ID}},
                ],
            )
        )
        theirs = respx.put(f"{MR_URL}/notes/99").mock(return_value=httpx.Response(403, json={}))
        ours = respx.put(f"{MR_URL}/notes/55").mock(return_value=httpx.Response(200, json={}))

        _gateway().post_review([], "👍 LGTM!", diff=DIFF)

        assert not theirs.called, "their note is not ours to edit"
        assert ours.called, "our own summary is still edited in place"

    @respx.mock
    def test_an_unknown_posting_identity_re_posts_rather_than_hides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail closed: with no login to compare, nothing on the MR counts as ours."""
        from lgtmaybe.core.findings import finding_identity

        _whoami(monkeypatch, httpx.Response(401))
        ours = f"<!-- lgtmaybe-identity:{finding_identity(FINDING)} -->"
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(200, json=[_discussion("d1", ours, BOT)])
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(
                200, json=[{"id": 55, "body": "<!-- lgtmaybe -->", "author": {"id": BOT_ID}}]
            )
        )
        edit = respx.put(f"{MR_URL}/notes/55").mock(return_value=httpx.Response(200, json={}))
        create = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))
        created = respx.post(f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(201, json={})
        )

        gateway = _gateway()
        gateway._diff_refs = MR_DETAIL["diff_refs"]
        gateway.post_review([FINDING], "1 finding", diff=DIFF)

        assert created.called, "a duplicate is better than a hidden finding"
        assert create.called and not edit.called, "never edit a note we can't prove is ours"

    @respx.mock
    def test_the_posting_identity_is_looked_up_once_per_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lookups = _whoami(monkeypatch, httpx.Response(200, json={"id": BOT_ID}))
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(
                200, json=[_discussion("d1", "<!-- lgtmaybe-finding:a -->", BOT)]
            )
        )
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(
                200, json=[{"id": 55, "body": "<!-- lgtmaybe -->", "author": {"id": BOT_ID}}]
            )
        )
        respx.put(f"{MR_URL}/notes/55").mock(return_value=httpx.Response(200, json={}))
        gateway = _gateway()

        gateway.count_open_finding_threads()
        gateway.post_review([], "👍 LGTM!", diff=DIFF)

        assert len(lookups) == 1


class TestConversation:
    @respx.mock
    def test_lists_human_and_bot_notes_but_not_system_notes(self) -> None:
        """GitLab's own "added 1 commit" notes are events, not anyone's overview."""
        respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"body": "added 1 commit", "system": True, "author": {"username": "a"}},
                    {"body": "walkthrough", "system": False, "author": {"username": "bot"}},
                    {"body": None},
                ],
            )
        )

        assert _gateway().list_conversation_comments() == [("bot", "walkthrough"), ("", "")]

    @respx.mock
    def test_shares_the_notes_fetch_with_the_upserts(self) -> None:
        _stub_post_routes()
        listed = respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
            return_value=httpx.Response(200, json=[])
        )
        gateway = _gateway()

        gateway.list_conversation_comments()
        gateway.post_diagram_comment("diagram")

        assert listed.call_count == 1


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
            "SupportsConversation",
            "SupportsIncremental",
            "SupportsFeedback",
        ):
            assert isinstance(gateway, getattr(ports, name)), name

    def test_does_not_claim_a_base_checkout(self) -> None:
        """Symbol lookup clones the base branch; nothing on GitLab offers that yet."""
        from lgtmaybe.core import ports

        assert not isinstance(GitLabGateway.__new__(GitLabGateway), ports.SupportsBaseCheckout)


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
        [("success", "success"), ("failure", "failed"), ("brand-new", "success")],
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
        body = json.loads(note.calls[0].request.content)["body"]
        assert FINDING.title in body, "the rejected inline finding is preserved in the summary"

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


class TestResolveFixedIsHonoured:
    """`resolve_fixed` is one setting across forges: GitHub threaded it through
    and GitLab did not, so turning it off left GitLab resolving anyway."""

    def _post_with(self, resolve_fixed: bool):
        _stub_context_routes()
        _stub_post_routes()
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "thread-1",
                        "notes": [
                            {
                                "id": 11,
                                "body": "old finding\n<!-- lgtmaybe-finding:abc -->",
                                "resolved": False,
                                "position": {"new_path": "app.py"},
                            }
                        ],
                    }
                ],
            )
        )
        respx.post(f"{MR_URL}/discussions/thread-1/notes").mock(
            return_value=httpx.Response(201, json={"id": 12})
        )
        resolve = respx.put(f"{MR_URL}/discussions/thread-1").mock(
            return_value=httpx.Response(200, json={})
        )
        gateway = GitLabGateway(
            host=HOST,
            repo=REPO,
            pr_number=MR_IID,
            token=TOKEN,
            client=httpx.Client(),
            resolve_fixed=resolve_fixed,
        )
        gateway.list_active_findings()
        gateway.set_validated_fixed_threads({"thread-1"})
        gateway.post_review([], "👍 LGTM!")
        return resolve

    @respx.mock
    def test_a_validated_thread_resolves_when_the_setting_is_on(self) -> None:
        assert self._post_with(True).called

    @respx.mock
    def test_nothing_resolves_when_the_setting_is_off(self) -> None:
        assert not self._post_with(False).called


def _forged_comment(target: ReviewFinding) -> str:
    """Our comment on an unrelated finding whose suggestion carries *target*'s ids.

    The suggestion is code applied with one click, so it is posted verbatim, and
    a model steered by a fork PR can write any finding's hidden ids into it.
    """
    from lgtmaybe.core.comment import render_inline_body
    from lgtmaybe.core.findings import finding_fingerprint, finding_identity

    ids = (
        f"<!-- lgtmaybe-finding:{finding_fingerprint(target.path, target.title)} -->\n"
        f"<!-- lgtmaybe-identity:{finding_identity(target)} -->"
    )
    decoy = target.model_copy(
        update={"path": "other.py", "title": "Unrelated", "suggestion": f"x = 1  {ids}"}
    )
    body = render_inline_body(decoy)
    assert ids in body, "the suggestion is posted verbatim"
    return body


class TestForgedFindingIds:
    """Only the ids appended after the suggestion fence name a discussion's finding."""

    @staticmethod
    def _discussions() -> None:
        respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "d1",
                        "notes": [
                            {
                                "id": 9,
                                "body": _forged_comment(FINDING),
                                "author": {"id": BOT_ID},
                                "resolved": False,
                                "position": {"new_path": "other.py"},
                            }
                        ],
                    }
                ],
            )
        )

    @respx.mock
    def test_a_forged_id_in_a_suggestion_does_not_suppress_the_real_finding(self) -> None:
        self._discussions()
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

        assert created.called, "the forged id suppressed a finding never posted"

    @respx.mock
    def test_active_findings_read_the_stamp_not_a_forged_suggestion(self) -> None:
        from lgtmaybe.core.findings import finding_fingerprint, finding_identity

        self._discussions()

        [active] = _gateway().list_active_findings()

        decoy = FINDING.model_copy(update={"path": "other.py", "title": "Unrelated"})
        assert active.fingerprint == finding_fingerprint("other.py", "Unrelated")
        assert active.identity == finding_identity(decoy)
