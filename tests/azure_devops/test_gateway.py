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
CONNECTION_DATA = "https://dev.azure.com/acme/_apis/connectionData"

# The identity the token authenticates as, and someone else's.
SELF_ID = "0f1e2d3c-self"
OTHER_ID = "9a8b7c6d-other"

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
        self.reject_status = 400
        # Refuse any thread carrying pullRequestThreadContext, as a host that
        # does not accept it would.
        self.reject_tracking = False
        # Status changes as (thread id, status), and replies as
        # (thread id, parent comment id, content), in the order they arrived.
        self.status_changes: list[tuple[int, Any]] = []
        self.replies: list[tuple[int, int, str]] = []
        self.status_http = 200
        self.writes: list[str] = []
        self.forbid_edits = False
        self.identity_status = 200
        # Responses (or exceptions) an Items fetch returns before the real file,
        # consumed in order per (path, commit).
        self.item_script: dict[tuple[str, str], list[httpx.Response | Exception]] = {}
        self.identity_calls = 0
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
        respx.route(method="PATCH", url__regex=rf"^{THREADS}/\d+\?").mock(
            side_effect=self._set_status
        )
        respx.route(method="PATCH", url__startswith=f"{THREADS}/").mock(
            side_effect=self._edit_comment
        )
        respx.route(method="POST", url__regex=rf"^{THREADS}/\d+/comments").mock(
            side_effect=self._reply
        )
        respx.get(CONNECTION_DATA).mock(side_effect=self._connection_data)

    def _connection_data(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.identity_calls += 1
        if self.identity_status != 200:
            return httpx.Response(self.identity_status, json={"message": "nope"})
        return httpx.Response(200, json={"authenticatedUser": {"id": SELF_ID}})

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
        script = self.item_script.get(key)
        if script:
            outcome = script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
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
            return httpx.Response(self.reject_status, json={"message": "refused"})
        if self.reject_tracking and "pullRequestThreadContext" in payload:
            return httpx.Response(400, json={"message": "invalid thread context"})
        self._next_id += 1
        thread = {
            "id": self._next_id,
            "status": payload.get("status"),
            "threadContext": payload.get("threadContext"),
            "pullRequestThreadContext": payload.get("pullRequestThreadContext"),
            "comments": [
                {
                    "id": 1,
                    "content": payload["comments"][0]["content"],
                    "commentType": "text",
                    "author": {"id": SELF_ID, "displayName": "Build Service"},
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

    def _set_status(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        thread_id = int(request.url.path.split("/")[-1])
        status = json.loads(request.content)["status"]
        self.writes.append("status")
        if self.status_http != 200:
            return httpx.Response(self.status_http, json={"message": "refused"})
        self.status_changes.append((thread_id, status))
        for thread in self.threads:
            if thread["id"] == thread_id:
                thread["status"] = status
        return httpx.Response(200, json={"id": thread_id, "status": status})

    def _reply(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        thread_id = int(request.url.path.split("/")[-2])
        payload = json.loads(request.content)
        self.writes.append("reply")
        self.replies.append((thread_id, payload["parentCommentId"], payload["content"]))
        return httpx.Response(200, json={"id": 2, "content": payload["content"]})


def _gateway(
    *, bearer: bool = False, marker_key: str | None = None, resolve_fixed: bool = True
) -> AzureDevOpsGateway:
    return AzureDevOpsGateway(
        host=HOST,
        repo=REPO,
        pr_number=PR_ID,
        token=TOKEN,
        client=httpx.Client(),
        bearer=bearer,
        marker_key=marker_key,
        resolve_fixed=resolve_fixed,
    )


def _thread(thread_id: int, content: str, **extra: Any) -> dict[str, Any]:
    """A thread whose root comment, by default, the token's own identity wrote."""
    return {
        "id": thread_id,
        "status": extra.pop("status", "active"),
        "comments": [
            {
                "id": 1,
                "content": content,
                "commentType": "text",
                "author": {"id": SELF_ID, "displayName": "Build Service"},
                **extra,
            }
        ],
    }


class TestAddressing:
    def test_the_repository_api_is_scoped_to_organisation_and_project(self) -> None:
        assert _gateway()._repo_api == REPO_API

    def test_the_legacy_host_addresses_the_organisation_by_subdomain(self) -> None:
        gateway = AzureDevOpsGateway(
            host="acme.visualstudio.com", repo="acme/Proj/api", pr_number=1, token="t"
        )
        assert gateway._repo_api == "https://acme.visualstudio.com/Proj/_apis/git/repositories/api"

    def test_the_identity_is_read_from_the_organisation(self) -> None:
        assert _gateway()._connection_data_url == CONNECTION_DATA
        legacy = AzureDevOpsGateway(
            host="acme.visualstudio.com", repo="acme/Proj/api", pr_number=1, token="t"
        )
        assert legacy._connection_data_url == "https://acme.visualstudio.com/_apis/connectionData"

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
        identity = [r for r in fake.requests if r.url.path.endswith("/_apis/connectionData")]
        assert len(identity) == 1
        # connectionData is a collection-level endpoint outside the versioned Git
        # API; no version string for it could be confirmed, so none is sent.
        assert "api-version" not in identity[0].url.params
        for request in fake.requests:
            assert TOKEN not in str(request.url)
            if request not in identity:
                assert request.url.params["api-version"] == "7.1"

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
    def test_an_integer_change_type_is_decoded_by_bit(self) -> None:
        """VersionControlChangeType flags: Edit 2 | Rename 8."""
        changes = [
            {"changeType": 2 | 8, "item": {"path": "/renamed.py"}, "originalPath": "/before.py"}
        ]
        FakeAzureDevOps(changes=changes).install()
        diff = _gateway().get_pr_context().diff

        assert "rename from before.py\nrename to renamed.py" in diff
        assert "-b = 2\n+b = 3" in diff

    @respx.mock
    def test_an_integer_delete_is_decoded_by_bit(self) -> None:
        changes = [{"changeType": 16, "item": {"path": "/gone.py"}}]
        FakeAzureDevOps(changes=changes).install()

        assert "deleted file mode 100644" in _gateway().get_pr_context().diff

    @respx.mock
    def test_a_missing_side_of_an_edit_is_named_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        files = {k: v for k, v in FILES.items() if k != ("/app.py", BASE_SHA)}
        FakeAzureDevOps(changes=CHANGES[:1], files=files).install()
        caplog.set_level(logging.WARNING)
        _gateway().get_pr_context()

        assert any("app.py" in r.getMessage() for r in caplog.records)

    @respx.mock
    def test_a_failing_commit_listing_leaves_the_intent_without_commits(self) -> None:
        FakeAzureDevOps().install()
        respx.get(f"{PR_API}/commits").mock(return_value=httpx.Response(500, text="boom"))
        ctx = _gateway().get_pr_context()

        assert ctx.commit_messages == []
        assert ctx.title == "Add a thing"

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
    def test_a_missing_merge_base_is_disclosed_not_diffed_against_the_target(self) -> None:
        """The target head carries other PRs' changes; diffing against it misplaces findings."""
        iterations = {
            "value": [
                {
                    "id": 2,
                    "sourceRefCommit": {"commitId": HEAD_SHA},
                    "targetRefCommit": {"commitId": "target789"},
                }
            ]
        }
        fake = FakeAzureDevOps()
        fake.install()
        respx.get(f"{PR_API}/iterations").mock(return_value=httpx.Response(200, json=iterations))

        with pytest.raises(DiffUnavailable, match="merge base"):
            _gateway().get_pr_context()
        assert not fake.item_requests

    @respx.mock
    def test_dependency_manifests_are_fetched_for_scanning_when_asked(self) -> None:
        FakeAzureDevOps().install()
        gateway = _gateway()
        gateway.set_scan_manifests(True)
        ctx = gateway.get_pr_context()

        assert ctx.scan_contents == {"package-lock.json": '{"lockfileVersion": 3}\n'}

    @respx.mock
    def test_manifests_stop_at_the_aggregate_ceiling(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(gateway_module, "_MAX_SCAN_TOTAL_BYTES", 30)
        changes = [
            {"changeType": "edit", "item": {"path": "/package-lock.json"}},
            {"changeType": "edit", "item": {"path": "/web/package-lock.json"}},
        ]
        files = {
            **FILES,
            ("/package-lock.json", HEAD_SHA): b'{"lockfileVersion": 3}\n',
            ("/web/package-lock.json", HEAD_SHA): b'{"lockfileVersion": 3}\n',
        }
        FakeAzureDevOps(changes=changes, files=files).install()
        caplog.set_level(logging.WARNING)
        gateway = _gateway()
        gateway.set_scan_manifests(True)
        ctx = gateway.get_pr_context()

        assert list(ctx.scan_contents) == ["package-lock.json"]
        assert any("web/package-lock.json" in r.getMessage() for r in caplog.records)

    @respx.mock
    def test_file_contents_reads_head_text_after_the_context_fetch(self) -> None:
        fake = FakeAzureDevOps()
        fake.install()
        gateway = _gateway()
        assert gateway.get_file_contents("app.py") is None, "no head known yet"

        gateway.get_pr_context()
        assert gateway.get_file_contents("app.py") == APP_NEW
        assert gateway.get_file_contents("missing.py") is None

    @respx.mock
    def test_file_contents_fetches_an_unchanged_file_at_the_head(self) -> None:
        """Reflection asks for files the diff never touched; those come from the API."""
        files = {**FILES, ("/lib/util.py", HEAD_SHA): b"def helper():\n    return 1\n"}
        fake = FakeAzureDevOps(files=files)
        fake.install()
        gateway = _gateway()
        gateway.get_pr_context()

        assert gateway.get_file_contents("lib/util.py") == "def helper():\n    return 1\n"
        assert ("/lib/util.py", HEAD_SHA) in fake.item_requests


class TestFetchResilience:
    """One file that will not come back must not cost the whole review."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        slept: list[float] = []
        monkeypatch.setattr(gateway_module, "_sleep", slept.append)
        return slept

    @respx.mock
    def test_throttling_is_retried_honouring_retry_after(self, _no_sleep: list[float]) -> None:
        fake = FakeAzureDevOps(changes=CHANGES[:1])
        fake.install()
        fake.item_script[("/app.py", HEAD_SHA)] = [
            httpx.Response(429, headers={"Retry-After": "2"}),
        ]
        ctx = _gateway().get_pr_context()

        assert '+password = "hunter2"' in ctx.diff
        assert _no_sleep == [2.0]

    @respx.mock
    def test_a_long_retry_after_is_capped(self, _no_sleep: list[float]) -> None:
        fake = FakeAzureDevOps(changes=CHANGES[:1])
        fake.install()
        fake.item_script[("/app.py", HEAD_SHA)] = [
            httpx.Response(429, headers={"Retry-After": "3600"}),
        ]
        _gateway().get_pr_context()

        assert _no_sleep == [gateway_module._MAX_RETRY_AFTER]

    @respx.mock
    def test_a_transient_server_error_and_a_timeout_are_retried(self) -> None:
        fake = FakeAzureDevOps(changes=CHANGES[:1])
        fake.install()
        fake.item_script[("/app.py", HEAD_SHA)] = [
            httpx.Response(503),
            httpx.ReadTimeout("slow"),
        ]
        ctx = _gateway().get_pr_context()

        assert '+password = "hunter2"' in ctx.diff

    @respx.mock
    def test_a_file_that_never_comes_back_degrades_alone(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = FakeAzureDevOps(changes=CHANGES[:2])
        fake.install()
        fake.item_script[("/app.py", HEAD_SHA)] = [
            httpx.Response(503) for _ in range(gateway_module._FETCH_ATTEMPTS)
        ]
        caplog.set_level(logging.WARNING)
        ctx = _gateway().get_pr_context()

        assert "Binary files a/app.py and b/app.py differ" in ctx.diff
        assert '+password = "hunter2"' not in ctx.diff, "no fabricated diff"
        assert "+print('hi')" in ctx.diff, "the other file is still reviewed"
        assert "app.py" not in ctx.file_contents
        message = next(r.getMessage() for r in caplog.records if "app.py" in r.getMessage())
        assert "new" in message and "503" in message

    @respx.mock
    def test_an_unfetched_file_is_named_in_the_summary(self) -> None:
        fake = FakeAzureDevOps(changes=CHANGES[:2])
        fake.install()
        fake.item_script[("/app.py", BASE_SHA)] = [
            httpx.Response(500) for _ in range(gateway_module._FETCH_ATTEMPTS)
        ]
        gateway = _gateway()
        gateway.post_review([], "0 findings", diff=gateway.get_pr_context().diff)

        summary = fake.created[-1]["comments"][0]["content"]
        assert "could not be read" in summary
        assert "`app.py`" in summary
        assert summary.endswith("<!-- lgtmaybe -->")

    @respx.mock
    def test_an_auth_failure_on_a_file_fails_loudly(self) -> None:
        """Azure Repos has no per-file read permission, so 401/403 means a bad token."""
        fake = FakeAzureDevOps(changes=CHANGES[:2])
        fake.install()
        fake.item_script[("/app.py", BASE_SHA)] = [httpx.Response(403)]

        with pytest.raises(httpx.HTTPStatusError):
            _gateway().get_pr_context()


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
    @pytest.mark.parametrize("status", [401, 403])
    def test_an_auth_failure_on_a_finding_thread_fails_the_review(self, status: int) -> None:
        """A token that cannot write threads is wrong, not one unplaceable finding:
        demoting every finding into the summary would bury them behind a 'success'."""
        fake = FakeAzureDevOps()
        fake.install()
        fake.reject_threads_at.add("/app.py")
        fake.reject_status = status
        gateway = _gateway()

        with pytest.raises(httpx.HTTPStatusError):
            gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

    @respx.mock
    def test_a_finding_thread_records_the_iteration_it_was_posted_at(self) -> None:
        """What lets a later run tell whether the flagged line has changed since."""
        fake = FakeAzureDevOps()
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert fake.created[0]["pullRequestThreadContext"] == {
            "changeTrackingId": 1,
            "iterationContext": {"firstComparingIteration": 1, "secondComparingIteration": 2},
        }

    @respx.mock
    def test_a_refused_iteration_context_is_retried_without_it(self) -> None:
        """Tracking is a nice-to-have: a host refusing it must not demote the finding."""
        fake = FakeAzureDevOps()
        fake.install()
        fake.reject_tracking = True
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        posted = [t for t in fake.created if t.get("threadContext")]
        assert len(posted) == 1
        assert "pullRequestThreadContext" not in posted[0]
        assert "Additional findings" not in fake.created[-1]["comments"][0]["content"]

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
        """A refused edit of our own comment must not fail the review."""
        fake = FakeAzureDevOps(threads=[_thread(5, "old\n\n<!-- lgtmaybe -->")])
        fake.install()
        fake.forbid_edits = True
        _gateway().post_review([], "👍 LGTM!", diff="")

        assert fake.created[-1]["comments"][0]["content"].startswith("👍 LGTM!")

    @respx.mock
    def test_an_unexpected_edit_failure_raises(self) -> None:
        """Only 403/404 fall through to a new thread; anything else is a real error."""
        fake = FakeAzureDevOps(threads=[_thread(5, "old\n\n<!-- lgtmaybe -->")])
        fake.install()
        respx.route(method="PATCH", url__startswith=f"{THREADS}/").mock(
            return_value=httpx.Response(500, json={"message": "boom"})
        )

        with pytest.raises(httpx.HTTPStatusError):
            _gateway().post_review([], "updated", diff="")
        assert not fake.created

    @respx.mock
    def test_a_planted_marker_is_never_an_edit_target(self) -> None:
        """Only a thread the token's own identity opened is ours to edit."""
        fake = FakeAzureDevOps(
            threads=[
                _thread(5, "planted\n\n<!-- lgtmaybe -->", author={"id": OTHER_ID}),
                _thread(6, "ours\n\n<!-- lgtmaybe -->"),
            ]
        )
        fake.install()
        _gateway().post_review([], "updated", diff="")

        assert [(tid, cid) for tid, cid, _c in fake.edited] == [(6, 1)]
        assert not [r for r in fake.requests if "/threads/5/" in r.url.path]
        assert not fake.created

    @respx.mock
    def test_without_an_identity_the_host_decides_who_may_edit(self) -> None:
        """Azure DevOps refuses edits to another author's comment, so trying is safe."""
        fake = FakeAzureDevOps(
            threads=[
                _thread(5, "planted\n\n<!-- lgtmaybe -->", author={"id": OTHER_ID}),
                _thread(6, "ours\n\n<!-- lgtmaybe -->"),
            ]
        )
        fake.install()
        fake.identity_status = 500
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
    def test_a_thread_planted_by_someone_else_does_not_suppress(self) -> None:
        """Path and title are public, so anyone could forge a finding's hidden id."""
        planted = self._posted_finding_thread(author={"id": OTHER_ID, "displayName": "Author"})
        fake = FakeAzureDevOps(threads=[planted])
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert len([t for t in fake.created if t.get("threadContext")]) == 1

    @respx.mock
    def test_a_thread_without_an_author_does_not_suppress(self) -> None:
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread(author={})])
        fake.install()
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert len([t for t in fake.created if t.get("threadContext")]) == 1

    @respx.mock
    def test_an_identity_lookup_failure_suppresses_nothing(self) -> None:
        """Fail safe: a finding may post twice, but is never hidden."""
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread()])
        fake.install()
        fake.identity_status = 401
        gateway = _gateway()
        gateway.post_review([FINDING], "1 finding", diff=gateway.get_pr_context().diff)

        assert len([t for t in fake.created if t.get("threadContext")]) == 1

    @respx.mock
    def test_the_identity_is_looked_up_once_per_gateway(self) -> None:
        fake = FakeAzureDevOps(threads=[self._posted_finding_thread()])
        fake.install()
        gateway = _gateway()
        diff = gateway.get_pr_context().diff
        gateway.post_review([FINDING], "1 finding", diff=diff)
        gateway.post_review([FINDING], "1 finding", diff=diff)
        gateway.post_describe_comment("A description")

        assert fake.identity_calls == 1

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


# What app.py held at iteration 1 ("stale"): line 2 is the one the finding flagged,
# and iteration 2 rewrote it.
APP_STALE = 'import os\npassword = "letmein"\n\ndef main():\n    pass\n'


class TestResolveOnFix:
    """A finding thread of ours whose finding is gone and whose line changed is fixed.

    Azure DevOps has no "outdated" flag, so the adapter records the iteration a
    thread was posted at and diffs the file from that iteration's head to ours.
    """

    @staticmethod
    def _posted(
        *,
        thread_id: int = 7,
        status: str = "active",
        iteration: int = 1,
        line: int = 2,
        path: str = "/app.py",
        title: str = "Hardcoded password",
        **extra: Any,
    ) -> dict[str, Any]:
        fingerprint = finding_fingerprint(path.lstrip("/"), title)
        thread = _thread(
            thread_id, f"old text\n<!-- lgtmaybe-finding:{fingerprint} -->", status=status, **extra
        )
        thread["threadContext"] = {
            "filePath": path,
            "rightFileStart": {"line": line, "offset": 1},
            "rightFileEnd": {"line": line, "offset": 1},
        }
        thread["pullRequestThreadContext"] = {
            "changeTrackingId": 1,
            "iterationContext": {
                "firstComparingIteration": 1,
                "secondComparingIteration": iteration,
            },
        }
        return thread

    @staticmethod
    def _fake(*threads: dict[str, Any], stale: str = APP_STALE) -> FakeAzureDevOps:
        fake = FakeAzureDevOps(
            threads=list(threads), files={**FILES, ("/app.py", "stale"): stale.encode()}
        )
        fake.install()
        return fake

    @staticmethod
    def _complete_run(
        gateway: AzureDevOpsGateway, findings: list[ReviewFinding] | None = None
    ) -> None:
        ctx = gateway.get_pr_context()
        gateway.mark_reviewed(ctx.head_sha)
        gateway.post_review(findings or [], "summary", diff=ctx.diff)

    @respx.mock
    def test_a_gone_finding_on_a_changed_line_is_resolved_then_replied_to(self) -> None:
        fake = self._fake(self._posted())
        self._complete_run(_gateway())

        assert fake.status_changes == [(7, "fixed")]
        assert fake.replies == [(7, 1, "✅ Looks resolved.")]
        assert fake.writes == ["status", "reply"], "resolved first, so a refusal leaves no reply"

    @respx.mock
    def test_a_finding_still_reported_stays_open(self) -> None:
        fake = self._fake(self._posted())
        self._complete_run(_gateway(), [FINDING])

        assert fake.status_changes == []

    @respx.mock
    def test_a_reworded_finding_still_reported_stays_open(self) -> None:
        """Matched on either hidden id: the identity survives a reworded title."""
        from lgtmaybe.core.findings import finding_identity

        thread = self._posted(title="Old wording")
        thread["comments"][0]["content"] += (
            f"\n<!-- lgtmaybe-identity:{finding_identity(FINDING)} -->"
        )
        fake = self._fake(thread)
        self._complete_run(_gateway(), [FINDING])

        assert fake.status_changes == []

    @respx.mock
    def test_a_line_that_did_not_change_stays_open(self) -> None:
        """Gone from this run's findings is not evidence of a fix on its own."""
        fake = self._fake(self._posted(), stale=APP_NEW)
        self._complete_run(_gateway())

        assert fake.status_changes == []

    @respx.mock
    def test_a_thread_posted_at_the_current_iteration_stays_open(self) -> None:
        fake = self._fake(self._posted(iteration=2))
        self._complete_run(_gateway())

        assert fake.status_changes == []

    @respx.mock
    def test_a_thread_without_an_iteration_stays_open(self) -> None:
        """Threads posted before tracking existed fail closed."""
        thread = self._posted()
        del thread["pullRequestThreadContext"]
        fake = self._fake(thread)
        self._complete_run(_gateway())

        assert fake.status_changes == []

    @respx.mock
    def test_a_file_deleted_since_is_resolved(self) -> None:
        files = {**FILES, ("/old.py", "stale"): b"a = 1\nb = 2\n"}
        fake = FakeAzureDevOps(threads=[self._posted(path="/old.py")], files=files)
        fake.install()
        self._complete_run(_gateway())

        assert fake.status_changes == [(7, "fixed")]

    @respx.mock
    def test_the_original_position_is_read_when_azure_devops_has_tracked_it(self) -> None:
        """Once a thread is re-tracked, trackingCriteria keeps where it was posted."""
        thread = self._posted(iteration=2, line=40)
        thread["pullRequestThreadContext"]["trackingCriteria"] = {
            "origFilePath": "/app.py",
            "origRightFileStart": {"line": 2, "offset": 1},
            "origRightFileEnd": {"line": 2, "offset": 1},
            "firstComparingIteration": 1,
            "secondComparingIteration": 1,
        }
        fake = self._fake(thread)
        self._complete_run(_gateway())

        assert fake.status_changes == [(7, "fixed")]

    @respx.mock
    def test_an_incomplete_run_resolves_nothing(self) -> None:
        """A failed lens's findings are missing, not fixed."""
        fake = self._fake(self._posted())
        gateway = _gateway()
        ctx = gateway.get_pr_context()
        gateway.mark_reviewed(None)
        gateway.post_review([], "partial", diff=ctx.diff)

        assert fake.status_changes == []

    @respx.mock
    def test_a_failure_notice_resolves_nothing(self) -> None:
        fake = self._fake(self._posted())
        gateway = _gateway()
        gateway.post_review([], "⚠️ lgtmaybe review failed: boom")

        assert fake.status_changes == []

    @respx.mock
    def test_someone_elses_thread_is_never_resolved(self) -> None:
        """Hidden ids are public, so a planted thread must not be closed for its author."""
        fake = self._fake(self._posted(author={"id": OTHER_ID, "displayName": "Author"}))
        self._complete_run(_gateway())

        assert fake.status_changes == []

    @respx.mock
    def test_an_unknown_identity_resolves_nothing(self) -> None:
        fake = self._fake(self._posted())
        fake.identity_status = 401
        self._complete_run(_gateway())

        assert fake.status_changes == []

    @respx.mock
    @pytest.mark.parametrize("status", ["wontFix", "closed", "fixed"])
    def test_a_thread_already_dealt_with_is_left_alone(self, status: str) -> None:
        fake = self._fake(self._posted(status=status))
        self._complete_run(_gateway())

        assert fake.status_changes == []

    @respx.mock
    def test_resolve_fixed_off_resolves_nothing(self) -> None:
        fake = self._fake(self._posted())
        self._complete_run(_gateway(resolve_fixed=False))

        assert fake.status_changes == []

    @respx.mock
    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refused_resolve_is_logged_once_and_never_fails_the_review(
        self, status: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = self._fake(self._posted(thread_id=7), self._posted(thread_id=8))
        fake.status_http = status
        with caplog.at_level(logging.WARNING):
            self._complete_run(_gateway())

        assert fake.writes == ["status"], "stops at the first refusal, and posts no reply"
        assert len([r for r in caplog.records if "resolve_fixed" in r.getMessage()]) == 1

    @respx.mock
    def test_a_validated_allowlist_decides_when_installed(self) -> None:
        """The incremental path's explicit verdicts win over the diff-derived rule."""
        fake = self._fake(self._posted(thread_id=7), self._posted(thread_id=8))
        gateway = _gateway()
        ctx = gateway.get_pr_context()
        active = gateway.list_active_findings()
        gateway.set_validated_fixed_threads({"8"})
        gateway.mark_reviewed(ctx.head_sha)
        gateway.post_review([], "summary", diff=ctx.diff)

        assert {f.thread_id for f in active} == {"7", "8"}
        assert fake.status_changes == [(8, "fixed")]


class TestActiveFindings:
    @respx.mock
    def test_lists_our_open_finding_threads_with_their_outdated_flag(self) -> None:
        threads = [
            TestResolveOnFix._posted(thread_id=7),
            TestResolveOnFix._posted(thread_id=8, iteration=2),
            TestResolveOnFix._posted(thread_id=9, status="fixed"),
            TestResolveOnFix._posted(thread_id=10, author={"id": OTHER_ID}),
            _thread(11, "a person's comment"),
        ]
        TestResolveOnFix._fake(*threads)
        gateway = _gateway()
        gateway.get_pr_context()
        active = {f.thread_id: f for f in gateway.list_active_findings()}

        assert set(active) == {"7", "8"}
        assert active["7"].outdated is True
        assert active["8"].outdated is False
        assert active["7"].path == "app.py"
        assert active["7"].comment_id == 1
        assert active["7"].fingerprint == finding_fingerprint("app.py", "Hardcoded password")

    @respx.mock
    def test_counts_our_open_finding_threads(self) -> None:
        threads = [
            TestResolveOnFix._posted(thread_id=7),
            TestResolveOnFix._posted(thread_id=8, status="pending"),
            TestResolveOnFix._posted(thread_id=9, status="wontFix"),
            TestResolveOnFix._posted(thread_id=10, author={"id": OTHER_ID}),
        ]
        TestResolveOnFix._fake(*threads)

        assert _gateway().count_open_finding_threads() == 2

    @respx.mock
    def test_reply_in_thread_answers_the_root_comment(self) -> None:
        fake = TestResolveOnFix._fake()
        _gateway().reply_in_thread("7", "noted")

        assert fake.replies == [(7, 1, "noted")]


class TestConversation:
    @respx.mock
    def test_lists_text_comments_with_their_authors(self) -> None:
        threads = [
            _thread(1, "```mermaid\nflowchart LR\n```", author={"displayName": "Dev"}),
            _thread(2, "Policy updated", commentType="system"),
            _thread(3, "no author", author={}),
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
            "SupportsThreadResolution",
        ):
            assert isinstance(gateway, getattr(ports, name)), name

    def test_does_not_claim_what_it_has_not_built(self) -> None:
        """Unbuilt capabilities stay unclaimed, so the CLI degrades instead of failing."""
        from lgtmaybe.core import ports

        gateway = AzureDevOpsGateway.__new__(AzureDevOpsGateway)
        for name in (
            "SupportsIncremental",
            "SupportsBaseCheckout",
            "SupportsLabels",
            "SupportsChecks",
            "SupportsFeedback",
        ):
            assert not isinstance(gateway, getattr(ports, name)), name
