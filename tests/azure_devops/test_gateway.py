"""Tests for AzureDevOpsGateway against a respx-faked Azure DevOps REST API.

Azure DevOps Repos differs from the other forges in two load-bearing ways:

1. **No unified diff endpoint.** A pull request exposes its iterations, each
   iteration's changed paths, and file contents at any commit. The adapter
   renders those into a ``git diff`` so the shared parser does the rest.
2. **Threads, not reviews.** Every finding is its own thread positioned by
   ``threadContext`` (``filePath`` plus right-file or left-file line ranges),
   and the summary is an ordinary thread whose first comment is edited in place.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx
import pytest
import respx

from lgtmaybe.azure_devops import AzureDevOpsGateway
from lgtmaybe.azure_devops import gateway as gateway_module
from lgtmaybe.core.findings import finding_fingerprint
from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.core.ports import DiffUnavailable

HOST = "dev.azure.com"
REPO = "acme/My Project/api"
PR_ID = 42
TOKEN = "ado-pat-secret-value"

REPO_API = "https://dev.azure.com/acme/My%20Project/_apis/git/repositories/api"
PR_API = f"{REPO_API}/pullRequests/{PR_ID}"
THREADS = f"{PR_API}/threads"

BASE_SHA = "base123"
HEAD_SHA = "head456"

PR_DETAIL = {
    "pullRequestId": PR_ID,
    "title": "Add a thing",
    "description": "This adds the thing.",
    "sourceRefName": "refs/heads/feature-branch",
    "targetRefName": "refs/heads/main",
}

ITERATIONS = {
    "count": 2,
    "value": [
        {
            "id": 1,
            "sourceRefCommit": {"commitId": "stale"},
            "commonRefCommit": {"commitId": "stale-base"},
        },
        {
            "id": 2,
            "sourceRefCommit": {"commitId": HEAD_SHA},
            "commonRefCommit": {"commitId": BASE_SHA},
            "targetRefCommit": {"commitId": "target789"},
        },
    ],
}

APP_OLD = "import os\n\ndef main():\n    pass\n"
APP_NEW = 'import os\npassword = "hunter2"\n\ndef main():\n    pass\n'

CHANGES: list[dict[str, Any]] = [
    {"changeTrackingId": 1, "changeType": "edit", "item": {"path": "/app.py"}},
    {"changeTrackingId": 2, "changeType": "add", "item": {"path": "/new.py"}},
    {"changeTrackingId": 3, "changeType": "delete", "item": {"path": "/gone.py"}},
    {
        "changeTrackingId": 4,
        "changeType": "rename, edit",
        "item": {"path": "/renamed.py"},
        "originalPath": "/before.py",
    },
    {"changeTrackingId": 5, "changeType": "edit", "item": {"path": "/package-lock.json"}},
    {"changeTrackingId": 6, "changeType": "edit", "item": {"path": "/src", "isFolder": True}},
]

FILES: dict[tuple[str, str], bytes] = {
    ("/app.py", BASE_SHA): APP_OLD.encode(),
    ("/app.py", HEAD_SHA): APP_NEW.encode(),
    ("/new.py", HEAD_SHA): b"print('hi')\n",
    ("/gone.py", BASE_SHA): b"old = 1\nalso = 2\n",
    ("/before.py", BASE_SHA): b"a = 1\nb = 2\n",
    ("/renamed.py", HEAD_SHA): b"a = 1\nb = 3\n",
    ("/package-lock.json", BASE_SHA): b"{}\n",
    ("/package-lock.json", HEAD_SHA): b'{"lockfileVersion": 3}\n',
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


class FakeAzureDevOps:
    """A stateful stand-in for the handful of endpoints the adapter calls."""

    def __init__(
        self,
        *,
        changes: list[dict[str, Any]] | None = None,
        files: dict[tuple[str, str], bytes] | None = None,
        threads: list[dict[str, Any]] | None = None,
        next_skip: int | None = None,
    ) -> None:
        self.changes = CHANGES if changes is None else changes
        self.files = FILES if files is None else files
        self.threads: list[dict[str, Any]] = threads if threads is not None else []
        self.next_skip = next_skip
        self.item_requests: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []
        self.edited: list[tuple[int, int, str]] = []
        self.reject_threads_at: set[str] = set()
        self.forbid_edits = False
        self.requests: list[httpx.Request] = []
        self._next_id = 1000

    def install(self) -> None:
        respx.get(PR_API).mock(side_effect=self._record(httpx.Response(200, json=PR_DETAIL)))
        respx.get(f"{PR_API}/iterations").mock(
            side_effect=self._record(httpx.Response(200, json=ITERATIONS))
        )
        respx.get(f"{PR_API}/iterations/2/changes").mock(side_effect=self._changes)
        respx.get(f"{PR_API}/commits").mock(
            side_effect=self._record(
                httpx.Response(
                    200,
                    json={"value": [{"commitId": "c1", "comment": "add a thing\n\nbody"}]},
                )
            )
        )
        respx.get(f"{REPO_API}/items").mock(side_effect=self._item)
        respx.get(THREADS).mock(side_effect=self._list_threads)
        respx.post(THREADS).mock(side_effect=self._create_thread)
        respx.route(method="PATCH", url__startswith=f"{THREADS}/").mock(
            side_effect=self._edit_comment
        )

    def _record(self, response: httpx.Response) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return response

        return handler

    def _changes(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload: dict[str, Any] = {"changeEntries": self.changes}
        if self.next_skip is not None:
            payload["nextSkip"] = self.next_skip
            payload["nextTop"] = 100
        return httpx.Response(200, json=payload)

    def _item(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        assert params["versionDescriptor.versionType"] == "commit"
        key = (params["path"], params["versionDescriptor.version"])
        self.item_requests.append(key)
        if key not in self.files:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, content=self.files[key])

    def _list_threads(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"count": len(self.threads), "value": self.threads})

    def _create_thread(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = json.loads(request.content)
        path = (payload.get("threadContext") or {}).get("filePath")
        if path in self.reject_threads_at:
            return httpx.Response(400, json={"message": "bad position"})
        self._next_id += 1
        thread = {
            "id": self._next_id,
            "status": payload.get("status"),
            "threadContext": payload.get("threadContext"),
            "comments": [
                {
                    "id": 1,
                    "content": payload["comments"][0]["content"],
                    "commentType": "text",
                    "author": {"displayName": "Build Service"},
                }
            ],
        }
        self.threads.append(thread)
        self.created.append(payload)
        return httpx.Response(200, json=thread)

    def _edit_comment(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.forbid_edits:
            return httpx.Response(403, json={"message": "not your comment"})
        parts = request.url.path.split("/")
        thread_id, comment_id = int(parts[-3]), int(parts[-1])
        content = json.loads(request.content)["content"]
        for thread in self.threads:
            if thread["id"] == thread_id:
                for comment in thread["comments"]:
                    if comment["id"] == comment_id:
                        comment["content"] = content
        self.edited.append((thread_id, comment_id, content))
        return httpx.Response(200, json={"id": comment_id, "content": content})


def _gateway(*, bearer: bool = False, marker_key: str | None = None) -> AzureDevOpsGateway:
    return AzureDevOpsGateway(
        host=HOST,
        repo=REPO,
        pr_number=PR_ID,
        token=TOKEN,
        client=httpx.Client(),
        bearer=bearer,
        marker_key=marker_key,
    )


def _thread(thread_id: int, content: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": thread_id,
        "status": extra.pop("status", "active"),
        "comments": [{"id": 1, "content": content, "commentType": "text", **extra}],
    }


class TestAddressing:
    def test_the_repository_api_is_scoped_to_organisation_and_project(self) -> None:
        assert _gateway()._repo_api == REPO_API

    def test_the_legacy_host_addresses_the_organisation_by_subdomain(self) -> None:
        gateway = AzureDevOpsGateway(
            host="acme.visualstudio.com", repo="acme/Proj/api", pr_number=1, token="t"
        )
        assert gateway._repo_api == "https://acme.visualstudio.com/Proj/_apis/git/repositories/api"

    def test_a_repo_that_is_not_three_segments_is_refused(self) -> None:
        with pytest.raises(ValueError, match="organisation/project/repository"):
            AzureDevOpsGateway(host=HOST, repo="acme/api", pr_number=1, token="t")


class TestTokenHandling:
    @respx.mock
    def test_a_personal_access_token_is_sent_as_basic_auth(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        _gateway().post_issue_comment("hello")

        expected = "Basic " + base64.b64encode(f":{TOKEN}".encode()).decode()
        assert fake.requests[-1].headers["Authorization"] == expected

    @respx.mock
    def test_the_pipeline_access_token_is_sent_as_a_bearer_token(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        _gateway(bearer=True).post_issue_comment("hello")

        assert fake.requests[-1].headers["Authorization"] == f"Bearer {TOKEN}"

    @respx.mock
    def test_every_request_pins_the_api_version_and_keeps_the_token_out_of_urls(
        self,
    ) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        _gateway().post_review([FINDING], "1 finding", diff=_gateway().get_pr_context().diff)

        assert fake.requests
        for request in fake.requests:
            assert request.url.params["api-version"] == "7.1"
            assert TOKEN not in str(request.url)

    @respx.mock
    def test_a_failure_never_echoes_the_token(self, caplog: pytest.LogCaptureFixture) -> None:
        respx.get(PR_API).mock(return_value=httpx.Response(401, text="unauthorised"))
        caplog.set_level(logging.DEBUG)

        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            _gateway().get_pr_context()

        assert TOKEN not in str(excinfo.value)
        assert TOKEN not in caplog.text
        assert TOKEN not in repr(_gateway())


class TestGetPRContext:
    @respx.mock
    def test_reads_the_latest_iteration_against_the_merge_base(self) -> None:
        FakeAzureDevOps().install()
        ctx = _gateway().get_pr_context()

        assert ctx.base_sha == BASE_SHA
        assert ctx.head_sha == HEAD_SHA
        assert ctx.repo == REPO
        assert ctx.pr_number == PR_ID

    @respx.mock
    def test_carries_the_stated_intent_for_the_intent_lens(self) -> None:
        FakeAzureDevOps().install()
        ctx = _gateway().get_pr_context()

        assert ctx.title == "Add a thing"
        assert ctx.description == "This adds the thing."
        assert ctx.commit_messages == ["add a thing"]
        assert ctx.head_branch == "feature-branch"

    @respx.mock
    def test_lists_every_changed_file_and_skips_folders(self) -> None:
        FakeAzureDevOps().install()
        ctx = _gateway().get_pr_context()

        assert ctx.changed_files == [
            "app.py",
            "new.py",
            "gone.py",
            "renamed.py",
            "package-lock.json",
        ]

    @respx.mock
    def test_assembles_a_unified_diff_for_edits_adds_deletes_and_renames(self) -> None:
        FakeAzureDevOps().install()
        diff = _gateway().get_pr_context().diff

        assert '--- a/app.py\n+++ b/app.py\n@@ -1,4 +1,5 @@\n import os\n+password = "hunter2"' in (
            diff
        )
        assert "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+print('hi')" in diff
        assert "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-old = 1" in diff
        assert "rename from before.py\nrename to renamed.py\n--- a/before.py\n+++ b/renamed.py" in (
            diff
        )
        assert "-b = 2\n+b = 3" in diff

    @respx.mock
    def test_skipped_files_are_named_but_never_fetched(self) -> None:
        """A lockfile is not reviewable, so its contents are not worth a request."""
        fake = FakeAzureDevOps()
        fake.install()
        diff = _gateway().get_pr_context().diff

        assert "Binary files a/package-lock.json and b/package-lock.json differ" in diff
        assert not any(path == "/package-lock.json" for path, _sha in fake.item_requests)

    @respx.mock
    def test_reads_old_text_at_the_base_and_new_text_at_the_head(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        _gateway().get_pr_context()

        assert ("/app.py", BASE_SHA) in fake.item_requests
        assert ("/app.py", HEAD_SHA) in fake.item_requests
        assert ("/before.py", BASE_SHA) in fake.item_requests
        assert ("/new.py", BASE_SHA) not in fake.item_requests, "an added file has no base"
        assert ("/gone.py", HEAD_SHA) not in fake.item_requests, "a deleted file has no head"

    @respx.mock
    def test_head_text_of_reviewable_files_feeds_context_expansion(self) -> None:
        FakeAzureDevOps().install()
        ctx = _gateway().get_pr_context()

        assert ctx.file_contents == {
            "app.py": APP_NEW,
            "new.py": "print('hi')\n",
            "renamed.py": "a = 1\nb = 3\n",
        }

    @respx.mock
    def test_a_file_over_the_size_cap_is_not_diffed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gateway_module, "_MAX_DIFF_BYTES", 10)
        FakeAzureDevOps().install()
        ctx = _gateway().get_pr_context()

        assert "Binary files a/app.py and b/app.py differ" in ctx.diff
        assert "app.py" not in ctx.file_contents

    @respx.mock
    def test_non_utf8_content_is_treated_as_binary(self) -> None:
        files = {**FILES, ("/app.py", HEAD_SHA): b"\xff\xfe\x00binary"}
        FakeAzureDevOps(files=files).install()
        ctx = _gateway().get_pr_context()

        assert "Binary files a/app.py and b/app.py differ" in ctx.diff

    @respx.mock
    def test_a_path_with_control_characters_is_left_out(self) -> None:
        """A newline in an attacker-chosen filename could forge diff headers."""
        changes = [
            *CHANGES[:1],
            {"changeType": "add", "item": {"path": "/evil\n+++ b/app.py"}},
        ]
        FakeAzureDevOps(changes=changes).install()
        ctx = _gateway().get_pr_context()

        assert ctx.changed_files == ["app.py"]
        assert "evil" not in ctx.diff

    @respx.mock
    def test_too_many_changed_files_is_disclosed_not_silently_truncated(self) -> None:
        many = [
            {"changeType": "edit", "item": {"path": f"/f{i}.py"}}
            for i in range(gateway_module._MAX_DIFF_FILES + 1)
        ]
        FakeAzureDevOps(changes=many).install()

        with pytest.raises(DiffUnavailable, match="files"):
            _gateway().get_pr_context()

    @respx.mock
    def test_a_further_page_of_changes_is_disclosed_too(self) -> None:
        FakeAzureDevOps(next_skip=300).install()

        with pytest.raises(DiffUnavailable):
            _gateway().get_pr_context()

    @respx.mock
    def test_an_iteration_without_commits_is_fatal(self) -> None:
        respx.get(PR_API).mock(return_value=httpx.Response(200, json=PR_DETAIL))
        respx.get(f"{PR_API}/iterations").mock(
            return_value=httpx.Response(200, json={"value": [{"id": 1}]})
        )

        with pytest.raises(RuntimeError, match="commit"):
            _gateway().get_pr_context()

    @respx.mock
    def test_dependency_manifests_are_fetched_for_scanning_when_asked(self) -> None:
        FakeAzureDevOps().install()
        gateway = _gateway()
        gateway.set_scan_manifests(True)
        ctx = gateway.get_pr_context()

        assert ctx.scan_contents == {"package-lock.json": '{"lockfileVersion": 3}\n'}

    @respx.mock
    def test_file_contents_reads_head_text_after_the_context_fetch(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        gateway = _gateway()
        assert gateway.get_file_contents("app.py") is None, "no head known yet"

        gateway.get_pr_context()
        assert gateway.get_file_contents("app.py") == APP_NEW
        assert gateway.get_file_contents("missing.py") is None


class TestPositions:
    @respx.mock
    def test_a_right_side_finding_uses_right_file_lines(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        thread = fake.created[0]
        assert thread["status"] == "active"
        assert thread["threadContext"] == {
            "filePath": "/app.py",
            "rightFileStart": {"line": 2, "offset": 1},
            "rightFileEnd": {"line": 2, "offset": 1},
        }
        content = thread["comments"][0]["content"]
        assert "Hardcoded password" in content
        assert f"lgtmaybe-finding:{finding_fingerprint('app.py', 'Hardcoded password')}" in content

    @respx.mock
    def test_a_left_side_finding_uses_left_file_lines(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        gateway = _gateway()
        diff = gateway.get_pr_context().diff
        removed = FINDING.model_copy(
            update={"path": "gone.py", "line": 2, "side": "LEFT", "anchor": "also = 2"}
        )
        gateway.post_review([removed], "1 finding", diff=diff)

        assert fake.created[0]["threadContext"] == {
            "filePath": "/gone.py",
            "leftFileStart": {"line": 2, "offset": 1},
            "leftFileEnd": {"line": 2, "offset": 1},
        }

    @respx.mock
    def test_an_unanchored_finding_is_demoted_into_the_summary(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        gateway = _gateway()
        guess = FINDING.model_copy(update={"line": 999, "anchored": False, "title": "Unplaceable"})
        gateway.post_review([guess], "1 finding", diff=gateway.get_pr_context().diff)

        assert len(fake.created) == 1, "only the summary is posted"
        summary = fake.created[0]
        assert "threadContext" not in summary
        assert "Additional findings" in summary["comments"][0]["content"]
        assert "Unplaceable" in summary["comments"][0]["content"]

    @respx.mock
    def test_a_rejected_position_is_demoted_and_the_review_still_posts(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        fake.reject_threads_at.add("/app.py")
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        summary = fake.created[-1]["comments"][0]["content"]
        assert "Additional findings" in summary
        assert "Hardcoded password" in summary

    @respx.mock
    def test_post_review_builds_the_diff_itself_when_not_given_one(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        _gateway().post_review([FINDING], "1 finding")

        assert fake.created[0]["threadContext"]["filePath"] == "/app.py"


class TestSummary:
    @respx.mock
    def test_the_summary_is_a_closed_general_thread_with_the_marker(self) -> None:
        """Closed, so a 'resolve all comments' branch policy never waits on it."""
        fake = FakeAzureDevOps()
        fake.install()
        summary = "0 findings · risk high\n<!-- lgtmaybe-risk:high -->"
        _gateway(marker_key="anthropic/m").post_review([], summary, diff="")

        created = fake.created[0]
        assert created["status"] == "closed"
        assert "threadContext" not in created
        content = created["comments"][0]["content"]
        assert "<!-- lgtmaybe-risk:high -->" in content
        assert content.endswith("<!-- lgtmaybe:anthropic/m -->")

    @respx.mock
    def test_a_rerun_edits_the_summary_and_posts_no_duplicates(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        gateway = _gateway()
        diff = gateway.get_pr_context().diff

        gateway.post_review([FINDING], "first run", diff=diff)
        _gateway().post_review([FINDING], "second run", diff=diff)

        assert len(fake.threads) == 2, "one finding thread and one summary thread"
        summary = next(t for t in fake.threads if not t.get("threadContext"))
        assert summary["comments"][0]["content"].startswith("second run")
        assert len(fake.edited) == 1

    @respx.mock
    def test_marker_families_never_clobber_each_other(self) -> None:
        fake = FakeAzureDevOps(threads=[_thread(5, "old summary\n\n<!-- lgtmaybe -->")])
        fake.install()
        gateway = _gateway()

        gateway.post_describe_comment("A description")
        gateway.post_diagram_comment("A diagram")

        assert not fake.edited
        assert [c["comments"][0]["content"] for c in fake.created] == [
            "A description\n\n<!-- lgtmaybe-describe -->",
            "A diagram\n\n<!-- lgtmaybe-diagram -->",
        ]

    @respx.mock
    def test_a_summary_it_may_not_edit_is_replaced_by_a_new_one(self) -> None:
        """Someone else's comment carrying our marker must not fail the review."""
        fake = FakeAzureDevOps(threads=[_thread(5, "planted\n\n<!-- lgtmaybe -->")])
        fake.install()
        fake.forbid_edits = True
        _gateway().post_review([], "👍 LGTM!", diff="")

        assert fake.created[-1]["comments"][0]["content"].startswith("👍 LGTM!")

    @respx.mock
    def test_a_planted_marker_does_not_hide_our_own_summary(self) -> None:
        """An earlier comment quoting our marker must not cost a duplicate each run."""
        fake = FakeAzureDevOps(
            threads=[
                _thread(5, "planted\n\n<!-- lgtmaybe -->"),
                _thread(6, "ours\n\n<!-- lgtmaybe -->"),
            ]
        )
        fake.install()
        real_edit = fake._edit_comment

        def edit_only_ours(request: httpx.Request) -> httpx.Response:
            if "/threads/5/" in request.url.path:
                return httpx.Response(403, json={"message": "not your comment"})
            return real_edit(request)

        respx.route(method="PATCH", url__startswith=f"{THREADS}/").mock(side_effect=edit_only_ours)
        _gateway().post_review([], "updated", diff="")

        assert [(tid, cid) for tid, cid, _c in fake.edited] == [(6, 1)]
        assert not fake.created

    @respx.mock
    def test_a_deleted_summary_thread_is_not_edited(self) -> None:
        thread = _thread(5, "old\n\n<!-- lgtmaybe -->")
        thread["isDeleted"] = True
        fake = FakeAzureDevOps(threads=[thread])
        fake.install()
        _gateway().post_review([], "fresh", diff="")

        assert not fake.edited
        assert fake.created[-1]["comments"][0]["content"].startswith("fresh")

    @respx.mock
    def test_post_issue_comment_opens_a_general_thread(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        _gateway().post_issue_comment("Review failed: boom")

        created = fake.created[0]
        assert created["comments"][0]["content"] == "Review failed: boom"
        assert "threadContext" not in created


class TestDedupe:
    @staticmethod
    def _posted_finding_thread(status: str = "active", **extra: Any) -> dict[str, Any]:
        fingerprint = finding_fingerprint("app.py", "Hardcoded password")
        return _thread(
            7, f"old text\n<!-- lgtmaybe-finding:{fingerprint} -->", status=status, **extra
        )

    @respx.mock
    def test_a_finding_already_posted_is_not_posted_twice(self) -> None:
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread()])
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert [t for t in fake.created if t.get("threadContext")] == []

    @respx.mock
    def test_a_finding_the_author_wont_fix_stays_dismissed(self) -> None:
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread(status="wontFix")])
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert [t for t in fake.created if t.get("threadContext")] == []

    @respx.mock
    def test_a_fixed_finding_that_returns_is_reported_again(self) -> None:
        """Mirrors GitLab: a resolved thread no longer suppresses its finding."""
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread(status="fixed")])
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert len([t for t in fake.created if t.get("threadContext")]) == 1

    @respx.mock
    def test_a_deleted_thread_does_not_suppress_its_finding(self) -> None:
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread(isDeleted=True)])
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert len([t for t in fake.created if t.get("threadContext")]) == 1

    @respx.mock
    def test_dedupe_consumes_each_existing_finding_once(self) -> None:
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread()])
        fake.install()
        diff = (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            '@@ -1,1 +1,3 @@\n import os\n+password = "hunter2"\n+password = "hunter2"\n'
        )
        findings = [FINDING, FINDING.model_copy(update={"line": 3})]
        _gateway().post_review(findings, "2 findings", diff=diff)

        posted = [t for t in fake.created if t.get("threadContext")]
        assert len(posted) == 1
        assert posted[0]["threadContext"]["rightFileStart"]["line"] == 3


class TestConversation:
    @respx.mock
    def test_lists_text_comments_with_their_authors(self) -> None:
        threads = [
            _thread(1, "```mermaid\nflowchart LR\n```", author={"displayName": "Dev"}),
            _thread(2, "Policy updated", commentType="system"),
            _thread(3, "no author"),
        ]
        FakeAzureDevOps(threads=threads).install()

        assert _gateway().list_conversation_comments() == [
            ("Dev", "```mermaid\nflowchart LR\n```"),
            ("", "no author"),
        ]


class TestCapabilities:
    def test_declares_the_capabilities_it_implements(self) -> None:
        from lgtmaybe.core import ports

        gateway = AzureDevOpsGateway.__new__(AzureDevOpsGateway)
        for name in (
            "SupportsFileContents",
            "SupportsDescribe",
            "SupportsDiagram",
            "SupportsConversation",
        ):
            assert isinstance(gateway, getattr(ports, name)), name

    def test_does_not_claim_what_it_has_not_built(self) -> None:
        """Unbuilt capabilities stay unclaimed, so the CLI degrades instead of failing."""
        from lgtmaybe.core import ports

        gateway = AzureDevOpsGateway.__new__(AzureDevOpsGateway)
        for name in (
            "SupportsIncremental",
            "SupportsThreadResolution",
            "SupportsBaseCheckout",
            "SupportsLabels",
            "SupportsChecks",
            "SupportsFeedback",
        ):
            assert not isinstance(gateway, getattr(ports, name)), name
