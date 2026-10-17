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
from tests.fakes import submission

HOST = "gitea.example.com"
REPO = "owner/repo"
PR_NUMBER = 7
TOKEN = "gitea_test_token"

API = f"https://{HOST}/api/v1/repos/{REPO}"
PR_URL = f"{API}/pulls/{PR_NUMBER}"
# The login conftest's identity fixture answers for `GET /api/v1/user`.
BOT = "lgtmaybe-bot"

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
    def test_lists_every_file_when_the_server_caps_pages_below_the_limit(self) -> None:
        # MAX_RESPONSE_ITEMS = 30: every page is shorter than the limit=50 asked.
        _stub_context_routes()
        names = [f"f{i}.py" for i in range(80)]

        def files_page(request: httpx.Request) -> httpx.Response:
            start = (int(request.url.params["page"]) - 1) * 30
            rows = [{"filename": n} for n in names[start : start + 30]]
            return httpx.Response(200, json=rows, headers={"X-Total-Count": "80"})

        respx.route(method="GET", url__startswith=f"{PR_URL}/files").mock(side_effect=files_page)

        ctx = _gateway(httpx.Client()).get_pr_context()

        assert ctx.changed_files == names

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
        _gateway(httpx.Client()).post_review(submission([finding], "1 finding", diff=DIFF))

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
        _gateway(httpx.Client()).post_review(submission([finding], "1 finding", diff=DIFF))

        import json as _json

        comment = _json.loads(reviews.calls[0].request.content)["comments"][0]
        assert comment["old_position"] == 1
        assert "new_position" not in comment

    @respx.mock
    def test_the_summary_keeps_the_risk_marker(self) -> None:
        """The engine stamps the risk verdict; the adapter must post it verbatim."""
        respx.route(method="GET", url__startswith=f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        create = respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 100})
        )

        summary = "0 findings · risk high\n<!-- lgtmaybe-risk:high -->"
        _gateway(httpx.Client()).post_review(submission([], summary, diff=DIFF))

        import json as _json

        body = _json.loads(create.calls[0].request.content)["body"]
        assert "<!-- lgtmaybe-risk:high -->" in body

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
                    {"id": 99, "body": "old summary\n\n<!-- lgtmaybe -->", "user": {"login": BOT}},
                ],
            )
        )
        edit = respx.patch(f"{API}/issues/comments/99").mock(
            return_value=httpx.Response(200, json={"id": 99})
        )
        create = respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 100})
        )

        _gateway(httpx.Client()).post_review(submission([], "👍 LGTM!", diff=DIFF))

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
            return_value=httpx.Response(200, json=[{"id": 5, "user": {"login": BOT}}])
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

        _gateway(httpx.Client()).post_review(submission([finding], "1 finding", diff=DIFF))

        assert not reviews.called, "the only finding was already on the PR"
        assert listed.call_count == 1

    @respx.mock
    def test_dedupe_consumes_each_existing_finding_once(self) -> None:
        from lgtmaybe.core.findings import finding_fingerprint

        already = finding_fingerprint("app.py", "Hardcoded password")
        respx.get(f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[{"id": 5, "user": {"login": BOT}}])
        )
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

        _gateway(httpx.Client()).post_review(
            submission(findings, "2 findings", diff=duplicate_diff)
        )

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
            return_value=httpx.Response(
                200, json=[{"id": i, "user": {"login": BOT}} for i in (5, 6, 7)]
            )
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

        _gateway(httpx.Client()).post_review(submission([finding], "1 finding", diff=DIFF))

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
        _gateway(httpx.Client()).post_review(submission([finding], "1 finding", diff=DIFF))

        import json as _json

        body = _json.loads(create.calls[0].request.content)["body"]
        assert "Additional findings" in body
        assert "Unplaceable" in body
        assert not reviews.called


_COMMAND_INJECTION_DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
+os.system(request.args["cmd"])

 def main():
"""


def _whoami(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list[httpx.Request]:
    """Answer `GET /api/v1/user` with *response*, recording each lookup."""
    original = httpx.Client.send
    lookups: list[httpx.Request] = []

    def send(client: httpx.Client, request: httpx.Request, **kwargs: object) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v1/user":
            lookups.append(request)
            response.request = request
            return response
        return original(client, request, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.Client, "send", send)
    return lookups


def _command_injection() -> ReviewFinding:
    return ReviewFinding(
        path="app.py",
        line=2,
        side="RIGHT",
        severity=Severity.critical,
        title="Command injection",
        body="RCE",
        anchor='os.system(request.args["cmd"])',
        category="security",
        anchored=True,
    )


class TestAuthorship:
    """Hidden ids and markers count only on comments the posting identity wrote.

    Anyone can type ``<!-- lgtmaybe-identity:… -->``: the identity is a hash of
    path, lens and anchor with no secret, so a PR author can precompute it, post
    it in a review of their own, and then push the line it describes.
    """

    @respx.mock
    def test_a_forged_id_in_someone_elses_review_does_not_hide_a_finding(self) -> None:
        from lgtmaybe.core.findings import finding_identity

        finding = _command_injection()
        forged = f"nice <!-- lgtmaybe-identity:{finding_identity(finding)} -->"
        respx.get(f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "user": {"login": "pr-author"}}])
        )
        respx.get(f"{PR_URL}/reviews/1/comments").mock(
            return_value=httpx.Response(
                200, json=[{"body": forged, "user": {"login": "pr-author"}}]
            )
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        _gateway(httpx.Client()).post_review(
            submission([finding], "1 finding", diff=_COMMAND_INJECTION_DIFF)
        )

        assert reviews.called, "a stranger's copy of our id must not suppress the finding"
        posted = json.loads(reviews.calls[0].request.content)["comments"]
        assert "Command injection" in posted[0]["body"]

    @respx.mock
    def test_someone_elses_comment_carrying_our_marker_is_never_edited(self) -> None:
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 55, "body": "lol <!-- lgtmaybe -->", "user": {"login": "pr-author"}},
                    {"id": 56, "body": "old summary\n\n<!-- lgtmaybe -->", "user": {"login": BOT}},
                ],
            )
        )
        theirs = respx.patch(f"{API}/issues/comments/55").mock(
            return_value=httpx.Response(403, json={})
        )
        ours = respx.patch(f"{API}/issues/comments/56").mock(
            return_value=httpx.Response(200, json={})
        )

        _gateway(httpx.Client()).post_review(submission([], "👍 LGTM!", diff=DIFF))

        assert not theirs.called, "their comment is not ours to edit"
        assert ours.called, "our own summary is still edited in place"

    @respx.mock
    def test_an_unknown_posting_identity_re_posts_rather_than_hides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail closed: with no login to compare, nothing on the PR counts as ours."""
        from lgtmaybe.core.findings import finding_identity

        finding = _command_injection()
        _whoami(monkeypatch, httpx.Response(500))
        respx.get(f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "user": {"login": BOT}}])
        )
        respx.get(f"{PR_URL}/reviews/1/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "body": f"<!-- lgtmaybe-identity:{finding_identity(finding)} -->",
                        "user": {"login": BOT},
                    }
                ],
            )
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(
                200, json=[{"id": 56, "body": "<!-- lgtmaybe -->", "user": {"login": BOT}}]
            )
        )
        edit = respx.patch(f"{API}/issues/comments/56").mock(
            return_value=httpx.Response(200, json={})
        )
        create = respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 2})
        )
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        _gateway(httpx.Client()).post_review(
            submission([finding], "1 finding", diff=_COMMAND_INJECTION_DIFF)
        )

        assert reviews.called, "a duplicate is better than a hidden finding"
        assert create.called and not edit.called, "never edit a comment we can't prove is ours"

    @respx.mock
    def test_the_posting_identity_is_looked_up_once_per_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lookups = _whoami(monkeypatch, httpx.Response(200, json={"login": BOT}))
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 56, "body": "<!-- lgtmaybe -->", "user": {"login": BOT}},
                    {"id": 57, "body": "<!-- lgtmaybe-describe -->", "user": {"login": BOT}},
                ],
            )
        )
        summary = respx.patch(f"{API}/issues/comments/56").mock(
            return_value=httpx.Response(200, json={})
        )
        describe = respx.patch(f"{API}/issues/comments/57").mock(
            return_value=httpx.Response(200, json={})
        )
        gateway = _gateway(httpx.Client())

        gateway.post_review(submission([], "👍 LGTM!", diff=DIFF))
        gateway.post_describe_comment("description")

        assert summary.called and describe.called
        assert len(lookups) == 1


class TestConversation:
    @respx.mock
    def test_lists_pr_comments_with_their_authors(self) -> None:
        """What the existing-overview scan reads before an automatic overview."""
        respx.get(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 1, "body": "```mermaid\nflowchart LR\n```", "user": {"login": "bot"}},
                    {"id": 2, "body": None},
                ],
            )
        )
        gateway = _gateway(httpx.Client())

        assert gateway.list_conversation_comments() == [
            ("bot", "```mermaid\nflowchart LR\n```"),
            ("", ""),
        ]


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
            "SupportsConversation",
            "SupportsPermissions",
            "SupportsScanManifests",
        ):
            assert isinstance(gateway, getattr(ports, name)), name

    def test_does_not_claim_what_gitea_cannot_do(self) -> None:
        """Gitea cannot reply in a review thread, so the validated-resolve flow is not claimed.

        Resolve-on-fix still runs, inside ``post_review`` (see TestResolveOnFix).
        """
        from lgtmaybe.core import ports

        gateway = GiteaGateway.__new__(GiteaGateway)
        assert not isinstance(gateway, ports.SupportsThreadResolution)


class TestPermissions:
    """Who may run a slash command: a collaborator with write access, or more."""

    @respx.mock
    @pytest.mark.parametrize(
        ("permission", "allowed"),
        [("owner", True), ("admin", True), ("write", True), ("read", False), ("none", False)],
    )
    def test_reads_the_collaborator_permission(self, permission: str, allowed: bool) -> None:
        respx.get(f"{API}/collaborators/someone/permission").mock(
            return_value=httpx.Response(200, json={"permission": permission})
        )
        assert _gateway(httpx.Client()).has_repo_write("someone") is allowed

    @respx.mock
    def test_fails_closed_when_the_lookup_fails(self) -> None:
        """A non-collaborator is a 403/404 here — never a writer."""
        respx.get(f"{API}/collaborators/stranger/permission").mock(
            return_value=httpx.Response(403, json={"message": "forbidden"})
        )
        assert _gateway(httpx.Client()).has_repo_write("stranger") is False

    @respx.mock
    def test_a_refused_lookup_names_the_token_as_the_likely_cause(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Forgejo's automatic job token gets a 403 here, while a personal token
        reads any account's permission (a stranger answers "read"). So a 403
        points at the token, and the log says so rather than blaming the
        commenter."""
        respx.get(f"{API}/collaborators/maintainer/permission").mock(
            return_value=httpx.Response(403, json={"message": "token does not have the scope"})
        )
        with caplog.at_level("WARNING", logger="lgtmaybe.gitea.gateway"):
            assert _gateway(httpx.Client()).has_repo_write("maintainer") is False
        assert "cannot read collaborator permissions" in caplog.text
        assert "personal access token" in caplog.text


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
    @respx.mock
    def test_a_forged_id_in_a_suggestion_does_not_suppress_the_real_finding(self) -> None:
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
        respx.get(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json=[{"id": 5}]))
        respx.get(f"{PR_URL}/reviews/5/comments").mock(
            return_value=httpx.Response(200, json=[{"body": _forged_comment(finding)}])
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        _gateway(httpx.Client()).post_review(submission([finding], "1 finding", diff=DIFF))

        assert reviews.called, "the forged id suppressed a finding never posted"
        posted = json.loads(reviews.calls[0].request.content)["comments"]
        assert any("Hardcoded password" in c["body"] for c in posted)


class TestResolveOnFix:
    """Gitea 1.26+ resolves a review comment; ours is resolved once its finding is fixed.

    Fixed means two things together, as on every forge: this complete run no
    longer produced the finding, and the line it was posted on has changed since.
    Gitea has no "outdated" flag, so the file is read at the comment's commit and
    at the head.
    """

    OLD = 'import os\npassword = "hunter2"\n\ndef main():\n'
    FIXED = 'import os\npassword = os.environ["PASSWORD"]\n\ndef main():\n'

    @staticmethod
    def _b64(text: str) -> dict[str, str]:
        import base64

        return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}

    def _comment(self, **overrides: object) -> dict[str, object]:
        from lgtmaybe.core.findings import finding_fingerprint

        fingerprint = finding_fingerprint("app.py", "Hardcoded password")
        comment: dict[str, object] = {
            "id": 31,
            "body": f"Hardcoded password\n<!-- lgtmaybe-finding:{fingerprint} -->",
            "path": "app.py",
            "position": 2,
            "original_position": 0,
            "commit_id": "old111",
            "resolver": None,
            "user": {"login": BOT},
        }
        comment.update(overrides)
        return comment

    def _stub(
        self,
        *,
        head_text: str | None = None,
        comments: list[dict[str, object]] | None = None,
        review_author: str = BOT,
        resolve_status: int = 204,
    ) -> respx.Route:
        """Context, one past review holding ``comments``, the summary, and the resolve route."""
        if head_text is None:
            respx.route(
                method="GET",
                url__startswith=f"{API}/contents/",
                params__contains={"ref": "head456"},
            ).mock(return_value=httpx.Response(404, json={"message": "not found"}))
        else:
            respx.route(
                method="GET",
                url__startswith=f"{API}/contents/",
                params__contains={"ref": "head456"},
            ).mock(return_value=httpx.Response(200, json=self._b64(head_text)))
        respx.route(
            method="GET", url__startswith=f"{API}/contents/", params__contains={"ref": "old111"}
        ).mock(return_value=httpx.Response(200, json=self._b64(self.OLD)))
        _stub_context_routes()
        respx.get(f"{PR_URL}/reviews").mock(
            return_value=httpx.Response(200, json=[{"id": 5, "user": {"login": review_author}}])
        )
        respx.get(f"{PR_URL}/reviews/5/comments").mock(
            return_value=httpx.Response(
                200, json=comments if comments is not None else [self._comment()]
            )
        )
        respx.route(method="GET", url__startswith=f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{API}/issues/{PR_NUMBER}/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))
        return respx.route(method="POST", url__regex=rf"{API}/pulls/comments/\d+/resolve").mock(
            return_value=httpx.Response(resolve_status)
        )

    def _complete_run(
        self, findings: list[ReviewFinding] | None = None, **kwargs: object
    ) -> GiteaGateway:
        gateway = GiteaGateway(
            host=HOST, repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client(), **kwargs
        )
        ctx = gateway.get_pr_context()
        gateway.post_review(
            submission(findings or [], "👍 LGTM!", diff=DIFF, completed_sha=ctx.head_sha)
        )
        return gateway

    @staticmethod
    def _finding() -> ReviewFinding:
        return ReviewFinding(
            path="app.py",
            line=2,
            side="RIGHT",
            severity=Severity.high,
            title="Hardcoded password",
            body="Move it to the environment.",
            anchor='password = "hunter2"',
            anchored=True,
        )

    @respx.mock
    def test_a_fixed_finding_is_resolved(self) -> None:
        resolve = self._stub(head_text=self.FIXED)

        self._complete_run()

        assert resolve.call_count == 1
        assert resolve.calls[0].request.url.path.endswith("/pulls/comments/31/resolve")

    @respx.mock
    def test_a_finding_this_run_still_produced_stays_open(self) -> None:
        resolve = self._stub(head_text=self.FIXED)

        self._complete_run([self._finding()])

        assert not resolve.called

    @respx.mock
    def test_a_gone_finding_whose_line_did_not_change_stays_open(self) -> None:
        """Absent but untouched: the model missed it this time, it was not fixed."""
        resolve = self._stub(head_text="# moved down\n" + self.OLD)

        self._complete_run()

        assert not resolve.called

    @respx.mock
    def test_a_finding_whose_file_was_deleted_is_resolved(self) -> None:
        resolve = self._stub(head_text=None)

        self._complete_run()

        assert resolve.call_count == 1

    @respx.mock
    def test_an_incomplete_run_resolves_nothing(self) -> None:
        """A partial run or failure notice is missing findings, not reporting them fixed."""
        resolve = self._stub(head_text=self.FIXED)
        gateway = _gateway(httpx.Client())
        gateway.get_pr_context()

        gateway.post_review(submission([], "partial", diff=DIFF))

        assert not resolve.called

    @respx.mock
    def test_resolve_fixed_off_resolves_nothing(self) -> None:
        resolve = self._stub(head_text=self.FIXED)

        self._complete_run(resolve_fixed=False)

        assert not resolve.called

    @respx.mock
    def test_an_already_resolved_comment_is_left_alone(self) -> None:
        resolve = self._stub(
            head_text=self.FIXED, comments=[self._comment(resolver={"login": "alice"})]
        )

        self._complete_run()

        assert not resolve.called

    @respx.mock
    def test_a_left_side_comment_is_never_resolved(self) -> None:
        """Pushes do not change the base, so an old-file line never goes outdated."""
        resolve = self._stub(
            head_text=self.FIXED, comments=[self._comment(position=0, original_position=2)]
        )

        self._complete_run()

        assert not resolve.called

    @respx.mock
    def test_someone_elses_review_carrying_our_ids_is_never_resolved(self) -> None:
        resolve = self._stub(head_text=self.FIXED, review_author="mallory")

        self._complete_run()

        assert not resolve.called

    @respx.mock
    def test_a_server_without_the_resolve_api_stops_after_one_try(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Forgejo and Gitea before 1.26 answer 404: comments stay open, the review posts."""
        resolve = self._stub(
            head_text=self.FIXED,
            comments=[self._comment(), self._comment(id=32)],
            resolve_status=404,
        )

        self._complete_run()

        assert resolve.call_count == 1
        assert "1.26" in caplog.text

    @respx.mock
    def test_a_refused_resolve_stops_and_names_the_permission(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        resolve = self._stub(
            head_text=self.FIXED,
            comments=[self._comment(), self._comment(id=32)],
            resolve_status=403,
        )

        self._complete_run()

        assert resolve.call_count == 1
        assert "resolve_fixed: false" in caplog.text

    @respx.mock
    def test_a_finding_we_resolved_posts_again_when_it_comes_back(self) -> None:
        """Our resolve stops the comment suppressing; a regression is a new finding."""
        self._stub(head_text=self.OLD, comments=[self._comment(resolver={"login": BOT})])
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        self._complete_run([self._finding()])

        assert reviews.call_count == 1

    @respx.mock
    def test_a_finding_a_person_resolved_is_not_posted_again(self) -> None:
        """A person resolving it is a decision to leave it, so it keeps suppressing."""
        self._stub(head_text=self.OLD, comments=[self._comment(resolver={"login": "alice"})])
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        self._complete_run([self._finding()])

        assert not reviews.called

    @respx.mock
    def test_a_failed_identity_lookup_never_hides_a_finding_we_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a login nothing is trusted as ours, so a returning finding posts again."""
        _whoami(monkeypatch, httpx.Response(500))
        self._stub(head_text=self.OLD, comments=[self._comment(resolver={"login": BOT})])
        reviews = respx.post(f"{PR_URL}/reviews").mock(return_value=httpx.Response(200, json={}))

        self._complete_run([self._finding()])

        assert reviews.call_count == 1

    @respx.mock
    def test_the_cli_passes_the_resolve_fixed_setting_through(self) -> None:
        from lgtmaybe.cli import _GATEWAY_BUILDERS
        from lgtmaybe.core.forge import Forge, PRLocator
        from lgtmaybe.core.models import Provider, ReviewConfig

        resolve = self._stub(head_text=self.FIXED)
        cfg = ReviewConfig(provider=Provider.ollama, model="llama3", resolve_fixed=False)
        located = PRLocator(forge=Forge.gitea, host=HOST, repo=REPO, number=PR_NUMBER)
        gateway = _GATEWAY_BUILDERS[Forge.gitea](located, TOKEN, cfg)
        ctx = gateway.get_pr_context()
        gateway.post_review(submission([], "👍 LGTM!", diff=DIFF, completed_sha=ctx.head_sha))

        assert not resolve.called
