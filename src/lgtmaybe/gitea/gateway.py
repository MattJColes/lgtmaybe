"""Gitea REST adapter.

Gitea's API is close enough to GitHub's that most of this reads the same, and
everything it renders — badges, demoted sections, hidden ids — comes from
``core.comment`` rather than being reimplemented. Three differences are load
bearing, and they are why this is a separate adapter rather than a base-URL
switch on the GitHub one:

1. **Reviews are not editable.** GitHub updates its review body in place on a
   re-run; Gitea has no equivalent. So the summary lives in an ordinary issue
   comment, which *is* editable, and the review object carries only the inline
   comments.
2. **Positions, not sides.** A Gitea review comment is anchored with
   ``new_position`` (a line in the new file) or ``old_position`` (a line in the
   old file), where GitHub uses ``line`` + ``side``. lgtmaybe keeps GitHub's
   RIGHT/LEFT vocabulary internally and translates at this boundary.
3. **Comments resolve one at a time, with no replies.** An immutable review
   cannot be de-duplicated after the fact, so this adapter reads the hidden ids
   already on the PR and declines to post those findings again. Gitea 1.26+
   resolves a single review comment, but has no "outdated" flag and no way to
   reply in a thread, so it does not claim ``SupportsThreadResolution``. It
   resolves our comment itself, after a complete run, when the finding is gone
   and the file read at the comment's commit and at the head shows its line
   changed. A server without the endpoint (Forgejo, older Gitea) answers 404 and
   the comments stay open.

Also deliberately absent: incremental review (Gitea's compare API returns commit
metadata, not a unified diff, so there is nothing to review an increment from)
and base-branch checkout for symbol resolution. Both are optional capabilities,
so the CLI simply runs a full review without them.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx

from lgtmaybe.core.comment import (
    DESCRIBE_MARKER_FAMILY,
    DIAGRAM_MARKER_FAMILY,
    current_finding_keys,
    finding_keys,
    marker,
    partition_findings,
    render_broad,
    render_demoted,
    render_inline_body,
    unposted,
)
from lgtmaybe.core.diff import (
    CommentableLines,
    build_commentable_lines,
    is_reviewable,
    is_scannable_manifest,
    line_changed,
)
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import PRContext, ReviewFinding, ReviewSubmission
from lgtmaybe.core.paginate import paginate_pages

_log = get_logger(__name__)

# Matches the GitHub adapter: a cap on a hung socket, not on a slow-but-alive
# request. A self-hosted Gitea behind a slow proxy is the common case.
_TIMEOUT = 30.0

# Concurrency for the per-file head-content fetch. Independent GETs, so fetching
# them serially on a wide PR is pure round-trip latency.
_CONTENT_FETCH_WORKERS = 8

# Gitea pages most list endpoints at 50 by default; ask for the maximum.
_PAGE_LIMIT = 50


class _Unreadable(Enum):
    """A file side that could not be read as text (None means it is not there)."""

    unreadable = "unreadable"


_Text = str | _Unreadable | None


class GiteaGateway:
    """Gitea REST adapter.

    Args:
        host:      Gitea hostname, e.g. "gitea.example.com" (self-hosted is the norm).
        repo:      Full repo name, e.g. "owner/repo".
        pr_number: Pull-request index.
        token:     Gitea API token.
        client:    Injected httpx.Client; a default is created if omitted.
        resolve_fixed: Whether to resolve our comments whose finding was fixed.
    """

    def __init__(
        self,
        host: str,
        repo: str,
        pr_number: int,
        token: str,
        client: httpx.Client | None = None,
        marker_key: str | None = None,
        resolve_fixed: bool = True,
        scheme: str = "https",
    ) -> None:
        self._repo = repo
        self._pr_number = pr_number
        self._user_api = f"{scheme}://{host}/api/v1/user"
        self._api = f"{scheme}://{host}/api/v1/repos/{repo}"
        self._pr_api = f"{self._api}/pulls/{pr_number}"
        self._issue_api = f"{self._api}/issues/{pr_number}"
        self._headers = {
            "Authorization": f"token {token}",
            "Accept": "application/json",
        }
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        # Disjoint marker families, matching the GitHub adapter, so a summary
        # update never clobbers the description or the diagram.
        self._marker = marker("lgtmaybe", marker_key)
        self._describe_marker = marker(DESCRIBE_MARKER_FAMILY, marker_key)
        self._diagram_marker = marker(DIAGRAM_MARKER_FAMILY, marker_key)
        self._head_sha: str | None = None
        self._head_texts: dict[str, str] = {}
        self._scan_manifests = False
        self._resolve_fixed = resolve_fixed
        # The posting identity's login, looked up once (see _posting_login).
        self._login: str | None = None
        self._login_done = False

    # ------------------------------------------------------------------
    # ReviewGateway implementation
    # ------------------------------------------------------------------

    def get_pr_context(self) -> PRContext:
        """Fetch PR metadata, unified diff, changed files, and head file text."""
        meta = self._get_json(self._pr_api)
        try:
            base_sha: str = meta["base"]["sha"]
            head_sha: str = meta["head"]["sha"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                f"PR metadata for {self._repo}#{self._pr_number} is missing the "
                f"base/head SHA, an unexpected Gitea API response ({exc})."
            ) from exc
        self._head_sha = head_sha

        diff = self._fetch_pr_diff()
        changed_files = [item["filename"] for item in self._paginate(f"{self._pr_api}/files")]

        # Head-revision text so the engine can pad hunks with surrounding
        # context. Read-only API fetch — never a checkout (fork-safe) — and the
        # engine redacts it before it leaves the process.
        reviewable = [path for path in changed_files if is_reviewable(path)]
        scannable = (
            [path for path in changed_files if is_scannable_manifest(path)]
            if self._scan_manifests
            else []
        )
        file_contents: dict[str, str] = {}
        scan_contents: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=_CONTENT_FETCH_WORKERS) as pool:
            if scannable:
                fetched = pool.map(lambda p: (p, self._get_file_content(p, head_sha)), scannable)
                scan_contents = {path: text for path, text in fetched if text is not None}
            if reviewable:
                fetched = pool.map(lambda p: (p, self._get_file_content(p, head_sha)), reviewable)
                file_contents = {path: text for path, text in fetched if text is not None}
        self._head_texts = file_contents

        return PRContext(
            diff=diff,
            changed_files=changed_files,
            base_sha=base_sha,
            head_sha=head_sha,
            repo=self._repo,
            pr_number=self._pr_number,
            file_contents=file_contents,
            scan_contents=scan_contents,
            title=meta.get("title") or "",
            description=meta.get("body") or "",
            commit_messages=self._fetch_commit_subjects(),
            head_branch=(meta.get("head") or {}).get("ref") or "",
        )

    def post_review(self, submission: ReviewSubmission) -> None:
        """Post inline comments as a review, and the summary as an upserted comment.

        Split in two because a Gitea review cannot be edited afterwards: the
        summary has to live somewhere re-runnable, and an issue comment is that
        place. Findings already carrying one of our hidden ids on the PR are
        dropped before posting, which is what keeps a re-run from duplicating
        every inline comment it made last time. Last, our comments whose finding
        is now fixed are resolved, after everything else posted.

        No watermark is written, since incremental review is not offered. The
        submission's ``completed_sha`` is the gate for resolve-on-fix instead: a
        partial run or a failure notice is missing findings, not reporting them
        fixed, so neither may resolve a comment.
        """
        findings, summary, diff = submission.findings, submission.summary, submission.diff
        if diff is None and findings:
            diff = self._fetch_pr_diff()

        commentable: CommentableLines = build_commentable_lines(diff or "")
        inline, demoted, broad = self._partition_findings(findings, commentable)
        resolving = (
            self._resolve_fixed
            and submission.completed_sha is not None
            and submission.completed_sha == self._head_sha
        )
        comments = self._own_review_comments() if inline or resolving else []

        if inline:
            inline = unposted(inline, self._existing_finding_keys(comments))

        if inline:
            resp = self._client.post(
                f"{self._pr_api}/reviews",
                headers=self._headers,
                json={
                    "body": "",
                    "event": "COMMENT",
                    "comments": [comment for comment, _f in inline],
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()

        body = f"{summary}{render_demoted(demoted)}{render_broad(broad)}\n\n{self._marker}"
        self._upsert_comment(body, self._marker)
        if resolving:
            self._resolve_fixed_comments(findings, comments)

    def post_issue_comment(self, body: str) -> None:
        """Post a standalone comment to the PR conversation."""
        resp = self._client.post(
            f"{self._issue_api}/comments",
            headers=self._headers,
            json={"body": body},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Optional capabilities
    # ------------------------------------------------------------------

    def get_file_contents(self, path: str) -> str | None:
        """Head-revision text of one file, or None when it can't be read."""
        if self._head_sha is None:
            return None
        return self._get_file_content(path, self._head_sha)

    def post_describe_comment(self, body: str) -> None:
        """Upsert the structured description in its own marker family."""
        self._upsert_comment(f"{body}\n\n{self._describe_marker}", self._describe_marker)

    def post_diagram_comment(self, body: str, *, completed_sha: str | None = None) -> None:
        """Upsert the change diagram in its own marker family.

        ``completed_sha`` is accepted and ignored: it exists to drive incremental
        review, which this adapter does not offer.
        """
        self._upsert_comment(f"{body}\n\n{self._diagram_marker}", self._diagram_marker)

    def list_conversation_comments(self) -> list[tuple[str, str]]:
        """Every PR conversation comment as ``(author, body)``, oldest first."""
        return [
            ((comment.get("user") or {}).get("login") or "", comment.get("body") or "")
            for comment in self._list(f"{self._issue_api}/comments")
        ]

    def set_scan_manifests(self, enabled: bool) -> None:
        """Also fetch dependency-manifest text on the next context fetch."""
        self._scan_manifests = enabled

    def apply_pr_labels(self, labels: list[str]) -> None:
        """Add lgtmaybe's labels to the PR, best-effort.

        Gitea addresses labels by id, so this resolves names against the repo's
        label set and silently skips any that do not exist — creating labels on
        someone's repo is not this tool's business.
        """
        if not labels:
            return
        try:
            known = {item["name"]: item["id"] for item in self._paginate(f"{self._api}/labels")}
            wanted = [known[name] for name in labels if name in known]
            if wanted:
                resp = self._client.post(
                    f"{self._issue_api}/labels",
                    headers=self._headers,
                    json={"labels": wanted},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — labels must never fail a review
            _log.warning("applying labels failed: %s", exc)

    def create_check_run(self, head_sha: str, conclusion: str, title: str, summary: str) -> None:
        """Publish the review outcome as a Gitea commit status, best-effort."""
        try:
            resp = self._client.post(
                f"{self._api}/statuses/{head_sha}",
                headers=self._headers,
                json={
                    # Gitea's state vocabulary is narrower than GitHub's
                    # conclusions, but the one producer of `conclusion`
                    # (`review.fail_on_check`) emits only "success"/"failure" —
                    # so a full mapping table is entries for inputs that never
                    # arrive. Only "failure" reds the commit; anything else
                    # keeps the fail-open default a best-effort status wants.
                    "state": "failure" if conclusion == "failure" else "success",
                    "context": "lgtmaybe",
                    "description": title[:255],
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — a status must never fail a review
            _log.warning("creating commit status failed: %s", exc)

    def has_repo_write(self, login: str) -> bool:
        """Whether *login* has write (or higher) access — who may run a slash command.

        Fails closed: any lookup error (a non-collaborator is refused, not
        answered) means "not authorised".
        """
        try:
            permission = self._get_json(
                f"{self._api}/collaborators/{quote(login, safe='')}/permission"
            ).get("permission")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                # A personal token reads any account's permission (a stranger
                # answers "read"), so a refusal points at the token: Forgejo's
                # automatic job token is refused here.
                _log.warning(
                    "permission check for %s refused (403): this token cannot read "
                    "collaborator permissions, so slash commands are ignored. Map a "
                    "personal access token to GITEA_TOKEN to enable them.",
                    login,
                )
            else:
                _log.warning("permission check for %s failed: %s", login, exc)
            return False
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            _log.warning("permission check for %s failed: %s", login, exc)
            return False
        return permission in {"owner", "admin", "write"}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _partition_findings(
        findings: list[ReviewFinding],
        commentable: CommentableLines,
    ) -> tuple[
        list[tuple[dict[str, Any], ReviewFinding]],
        list[ReviewFinding],
        list[ReviewFinding],
    ]:
        """Split findings into inline comments, body-demoted, and broad findings.

        The placement rule is shared (``core.comment.partition_findings``); only
        the payload here is Gitea's position vocabulary.
        """

        def place(f: ReviewFinding) -> dict[str, Any]:
            comment: dict[str, Any] = {"path": f.path, "body": render_inline_body(f)}
            # RIGHT is a line in the new file, LEFT a line in the old one.
            if f.side == "LEFT":
                comment["old_position"] = f.line
            else:
                comment["new_position"] = f.line
            return comment

        return partition_findings(findings, commentable, place)

    def _posting_login(self) -> str | None:
        """The login this token posts as, looked up once; None when Gitea won't say.

        Hidden ids and markers are plain text anyone can type, so only a comment
        this identity wrote may count as ours. None makes every caller fail
        closed: dedupe treats nothing as posted (a duplicate, never a hidden
        finding) and an upsert creates rather than edits a stranger's comment.
        """
        if not self._login_done:
            self._login_done = True
            try:
                login = self._get_json(self._user_api).get("login")
            except Exception as exc:  # noqa: BLE001 — fail closed, see above
                _log.warning(
                    "looking up the posting identity failed (does the token have the "
                    "read:user scope?): %s",
                    exc,
                )
            else:
                self._login = login if isinstance(login, str) and login else None
        return self._login

    def _is_ours(self, item: dict[str, Any]) -> bool:
        """Whether a review or comment was written by the posting identity."""
        login = self._posting_login()
        return login is not None and (item.get("user") or {}).get("login") == login

    def _existing_finding_keys(self, comments: list[dict[str, Any]]) -> list[set[str]]:
        """Every hidden finding id already posted on this PR by us.

        ``comments`` come from ``_own_review_comments``, so only reviews the
        posting identity wrote count: anyone can paste our hidden ids (the
        identity is a prose-free hash a PR author can compute), and counting
        theirs would let them hide a finding before it is made. A comment its
        own author resolved (resolve-on-fix) stops suppressing, so a finding
        that comes back posts again; one a person resolved still suppresses, as
        their call to leave it. The check reads both logins off the comment, so
        it holds whether or not this run's identity lookup succeeded.
        """
        keys: list[set[str]] = []
        for comment in comments:
            author = (comment.get("user") or {}).get("login")
            if author and (comment.get("resolver") or {}).get("login") == author:
                continue
            if found := finding_keys(comment.get("body") or ""):
                keys.append(found)
        return keys

    def _own_review_comments(self) -> list[dict[str, Any]]:
        """Every inline comment on the reviews the posting identity wrote.

        Best-effort: a failure here means a duplicate comment and nothing
        resolved, which is far better than failing the whole review.
        """
        out: list[dict[str, Any]] = []
        try:
            # Gitea reviews are immutable, so every past lgtmaybe run left its
            # own review object and there is no flat "all review comments for
            # this PR" endpoint to read them back through. That makes the
            # per-review fetch a genuine API constraint — but the fetches are
            # independent, so they need not be serial: an active PR accumulates
            # enough review cycles for one round trip each to dominate.
            review_ids = [
                review["id"]
                for review in self._paginate(f"{self._pr_api}/reviews")
                if review.get("id") is not None and self._is_ours(review)
            ]
            with ThreadPoolExecutor(max_workers=_CONTENT_FETCH_WORKERS) as pool:
                for comments in pool.map(
                    lambda rid: self._list(f"{self._pr_api}/reviews/{rid}/comments"), review_ids
                ):
                    out.extend(comments)
        except Exception as exc:  # noqa: BLE001 — dedupe is best-effort
            _log.warning("reading existing review comments failed: %s", exc)
        return out

    def _resolve_fixed_comments(
        self, findings: list[ReviewFinding], comments: list[dict[str, Any]]
    ) -> None:
        """Resolve each of our open finding comments whose finding is now fixed.

        Fixed means this run no longer produced either hidden id and the line
        the comment sits on has changed. A 404 means the server has no resolve
        endpoint, and a 401 or 403 is the identity's, so either stops the rest.
        Never fails the review.
        """
        current = current_finding_keys(findings)
        for comment in comments:
            comment_id = comment.get("id")
            keys = finding_keys(comment.get("body") or "")
            if comment.get("resolver") or comment_id is None or not keys or keys & current:
                continue
            if not self._is_outdated(comment):
                continue
            try:
                resp = self._client.post(
                    f"{self._api}/pulls/comments/{comment_id}/resolve",
                    headers=self._headers,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (404, 405):
                    _log.warning(
                        "this server cannot resolve review comments (Gitea 1.26 or later "
                        "can; Forgejo cannot yet), so fixed findings stay open. Set "
                        "resolve_fixed: false to stop trying. (%s)",
                        exc,
                    )
                    return
                if status in (401, 403):
                    _log.warning(
                        "resolving review comments is not permitted for this identity, so "
                        "fixed findings stay open. Give the token write access to the "
                        "repository, or set resolve_fixed: false to stop trying. (%s)",
                        exc,
                    )
                    return
                _log.warning("resolving comment %s failed: %s", comment_id, exc)
            except Exception as exc:  # noqa: BLE001 — one comment never blocks the rest
                _log.warning("resolving comment %s failed: %s", comment_id, exc)

    def _is_outdated(self, comment: dict[str, Any]) -> bool:
        """Whether the line a finding comment was posted on has changed since.

        The comment's ``commit_id`` is the head it was posted against; the file
        is read there and at our head. Fails closed: an old-file (LEFT) comment
        (pushes do not change the base), an unreadable side, or any missing
        field reads as not outdated, so nothing is resolved without evidence.
        """
        head = self._head_sha
        posted_at = comment.get("commit_id")
        path = comment.get("path")
        line = comment.get("position")
        if not head or not isinstance(posted_at, str) or not posted_at or posted_at == head:
            return False
        if not isinstance(path, str) or not path or not isinstance(line, int):
            return False
        before = self._read_file(path, posted_at)
        if not isinstance(before, str):
            return False
        after = self._head_texts[path] if path in self._head_texts else self._read_file(path, head)
        if after is None:
            return True  # the file is gone from the head
        if not isinstance(after, str):
            return False
        return line_changed(before, after, line)

    def _upsert_comment(self, body: str, family: str) -> None:
        """Edit our previous comment in this marker family, or post a new one."""
        existing_id = self._find_comment(family)
        if existing_id is not None:
            resp = self._client.patch(
                f"{self._api}/issues/comments/{existing_id}",
                headers=self._headers,
                json={"body": body},
                timeout=_TIMEOUT,
            )
        else:
            resp = self._client.post(
                f"{self._issue_api}/comments",
                headers=self._headers,
                json={"body": body},
                timeout=_TIMEOUT,
            )
        resp.raise_for_status()

    def _find_comment(self, family: str) -> int | None:
        """The id of our existing comment in ``family``, or None.

        Ours means written by the posting identity — a stranger's comment that
        carries our marker is never edited (it would 403, or worse, succeed).
        """
        for comment in self._list(f"{self._issue_api}/comments"):
            if family in (comment.get("body") or "") and self._is_ours(comment):
                comment_id = comment.get("id")
                return int(comment_id) if comment_id is not None else None
        return None

    def _fetch_pr_diff(self) -> str:
        """The PR's unified diff. Gitea serves it from the API path directly."""
        resp = self._client.get(f"{self._pr_api}.diff", headers=self._headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    def _fetch_commit_subjects(self) -> list[str]:
        """Commit subject lines, feeding the intent lens. Never fails the review."""
        try:
            return [
                message.splitlines()[0]
                for item in self._paginate(f"{self._pr_api}/commits")
                if (message := (item.get("commit", {}).get("message") or "").strip())
            ]
        except Exception as exc:  # noqa: BLE001 — intent is a nice-to-have
            _log.warning("fetching commit subjects failed: %s", exc)
            return []

    def _get_file_content(self, path: str, ref: str) -> str | None:
        """One file's text at ``ref``, or None when absent or undecodable."""
        text = self._read_file(path, ref)
        return text if isinstance(text, str) else None

    def _read_file(self, path: str, ref: str) -> _Text:
        """One file's text at ``ref``: None when it is not there, else unreadable."""
        try:
            resp = self._client.get(
                # quote() the path, keeping `/` as a real separator — an
                # unescaped `#` opens a URL fragment, truncating the path and
                # discarding the `ref` query with the rest of the filename.
                f"{self._api}/contents/{quote(path)}",
                headers=self._headers,
                params={"ref": ref},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                return _Unreadable.unreadable
            payload = resp.json()
            content = payload.get("content")
            if not isinstance(content, str):
                return _Unreadable.unreadable
            # Gitea returns base64 like GitHub; a binary file simply won't decode
            # as UTF-8, and is not reviewable anyway.
            return base64.b64decode(content).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 — a missing file is not fatal
            _log.debug("fetching %s failed: %s", path, exc)
            return _Unreadable.unreadable

    def _get_json(self, url: str) -> Any:
        resp = self._client.get(url, headers=self._headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _list(self, url: str) -> list[dict[str, Any]]:
        """One unpaginated Gitea list response."""
        payload = self._get_json(url)
        return payload if isinstance(payload, list) else []

    def _paginate(self, url: str) -> list[dict[str, Any]]:
        """Every page of a Gitea list endpoint, flattened."""
        return paginate_pages(
            self._client,
            url,
            self._headers,
            page_param="limit",
            limit=_PAGE_LIMIT,
            timeout=_TIMEOUT,
        )
