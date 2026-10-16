"""Azure DevOps Repos REST adapter (api-version 7.1).

The fourth forge, and the second whose model genuinely differs. What it renders
(badges, demoted sections, hidden ids) comes from ``core.comment``, and the diff
it assembles is parsed by ``core.diffparse`` like every other host's. Three
differences are load bearing:

1. **No unified diff.** Azure DevOps serves a pull request's iterations, each
   iteration's changed paths, and any file's text at any commit, but no diff.
   The adapter reads the latest iteration's changes against the merge base
   (``commonRefCommit``), fetches both sides of each reviewable file through the
   Items API, and renders them with ``core.diffbuild`` into the ``git diff``
   every other forge returns. Unreviewable files (lockfiles, vendored,
   binaries) are named but never fetched, and a change list too long to
   assemble is refused as ``DiffUnavailable`` rather than truncated silently.
2. **Threads, not reviews.** Each finding is its own thread whose
   ``threadContext`` names the file and a right-file (new side) or left-file
   (old side) line range. The summary is a general thread, and its first comment
   is edited in place on a re-run. Non-finding threads are created ``closed`` so
   a "resolve all comments" branch policy never waits on a summary.
3. **Two token shapes.** A personal access token authenticates as basic auth
   with an empty user name; the pipeline's own ``System.AccessToken`` as a
   bearer token. Neither is ever logged or put in a URL.

Resolve-on-fix is offered, though Azure DevOps has no "the lines moved" flag
on a thread. Each finding thread records the iteration it was posted at
(``pullRequestThreadContext``), and a later run diffs the file from that
iteration's head to its own to tell whether the flagged line changed. Only our
own identity's threads are ever resolved, and only after a complete run.

Deliberately absent, and not claimed: incremental review, labels, commit
statuses, 👎 feedback, and base-branch checkout. The Azure DevOps API could
serve most of them; they are "not yet", and the CLI runs a full review without
them.
"""

from __future__ import annotations

import base64
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx

from lgtmaybe.core.comment import (
    DIAGRAM_MARKER_FAMILY,
    current_finding_keys,
    finding_keys,
    marker,
    partition_findings,
    render_broad,
    render_demoted,
    render_inline_body,
    render_unread_files,
    unposted,
)
from lgtmaybe.core.diff import (
    CommentableLines,
    build_commentable_lines,
    is_reviewable,
    is_scannable_manifest,
)
from lgtmaybe.core.diffbuild import binary_file_patch, file_patch, has_unsafe_path_chars
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import PRContext, ReviewFinding
from lgtmaybe.core.ports import DiffUnavailable

_log = get_logger(__name__)

_API_VERSION = "7.1"
_TIMEOUT = 30.0
_CONTENT_FETCH_WORKERS = 8

# The legacy host carries the organisation in its subdomain, so its collection
# URL has no organisation segment.
_LEGACY_HOST_SUFFIX = ".visualstudio.com"

# The diff is assembled one file at a time, two fetches per file. GitHub refuses
# a diff past 300 files; the same ceiling keeps a sprawling change from turning
# into hundreds of requests, and it is disclosed rather than truncated.
_MAX_DIFF_FILES = 300

# A file side larger than this is shown as "binary": past the default
# max_file_diff_lines it would be dropped by the engine anyway, and difflib's
# cost grows faster than linearly. Dependency manifests are scan-only and
# routinely larger (a package-lock.json), so they get their own ceiling.
_MAX_DIFF_BYTES = 1_000_000
_MAX_SCAN_BYTES = 8_000_000
# All manifests together: a few large lockfiles, not 300 of them, held at once.
# Manifests past this are skipped and logged; scanning is supplementary.
_MAX_SCAN_TOTAL_BYTES = 32_000_000

# Azure DevOps throttles readily, so an Items fetch answering 429 or 5xx, or
# timing out, is retried a bounded number of times before only that file is
# given up on. A server-supplied Retry-After is honoured up to a small cap, so
# one throttled file cannot stall the review for minutes.
_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF = 1.0
_MAX_RETRY_AFTER = 10.0
_sleep = time.sleep

# Finding threads are left open for the author to resolve; every other thread
# (summary, description, notices) is created closed, so it never holds a merge
# behind a "resolve all comments" branch policy.
_FINDING_STATUS = "active"
_NOTE_STATUS = "closed"

# A resolved finding that comes back is reported again (as on GitLab); a thread
# the author marked won't-fix, by-design, or closed stays dismissed.
_FIXED_STATUSES = frozenset({"fixed", 2})

# The statuses a finding thread is still open in: active, and pending (which
# Azure DevOps offers as "waiting for the author"). Everything else is dealt with.
_OPEN_STATUSES = frozenset({"active", 1, "pending", 6})

# Written into a thread after it is resolved, so the trail explains itself.
_RESOLVED_REPLY = "✅ Looks resolved."

# Azure DevOps numbers a thread's comments from 1, so its root comment is 1.
_ROOT_COMMENT_ID = 1

# Change types arrive as a comma-separated flag string ("rename, edit"), or as
# the integer flags they serialise from. The values are Azure DevOps'
# VersionControlChangeType enum (as published in microsoft/azure-devops-node-api,
# GitInterfaces.ts): None 0, Add 1, Edit 2, Encoding 4, Rename 8, Delete 16,
# Undelete 32. Encoding (4) is deliberately absent: it changes no lines.
_CHANGE_FLAGS = {1: "add", 2: "edit", 8: "rename", 16: "delete", 32: "undelete"}


class _FetchFailed(Exception):
    """An Items fetch that still failed after its retries (not an auth failure)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Unreadable(Enum):
    """A file side that exists but cannot be shown as text: binary or too large."""

    blob = "blob"


_Text = str | _Unreadable | None


@dataclass(frozen=True, slots=True)
class _Change:
    """One changed file: its path before (None when added) and after (None when deleted)."""

    old_path: str | None
    new_path: str | None

    @property
    def path(self) -> str:
        """The path a reviewer knows the file by: the new one unless it was deleted."""
        path = self.new_path if self.new_path is not None else self.old_path
        assert path is not None  # a change always has at least one side
        return path


class AzureDevOpsGateway:
    """Azure DevOps Repos REST adapter.

    Args:
        host:      ``dev.azure.com`` or a legacy ``<org>.visualstudio.com``.
        repo:      ``"organisation/project/repository"``, decoded.
        pr_number: Pull request id.
        token:     A personal access token, or the pipeline's System.AccessToken.
        client:    Injected httpx.Client; a default is created if omitted.
        bearer:    Send ``token`` as a bearer token (System.AccessToken) rather
                   than as basic auth (a personal access token).
        resolve_fixed: Whether to resolve our finding threads once they are fixed.
    """

    def __init__(
        self,
        host: str,
        repo: str,
        pr_number: int,
        token: str,
        client: httpx.Client | None = None,
        marker_key: str | None = None,
        scheme: str = "https",
        bearer: bool = False,
        resolve_fixed: bool = True,
    ) -> None:
        parts = repo.split("/")
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"An Azure DevOps repository is addressed as organisation/project/repository, "
                f"got {repo!r}."
            )
        org, project, name = parts
        if host.lower().endswith(_LEGACY_HOST_SUFFIX):
            collection = f"{scheme}://{host}"
        else:
            collection = f"{scheme}://{host}/{_segment(org)}"
        self._repo = repo
        self._pr_number = pr_number
        self._repo_api = f"{collection}/{_segment(project)}/_apis/git/repositories/{_segment(name)}"
        # Who the token authenticates as. An organisation-level endpoint that
        # answers for a personal access token and for the pipeline's
        # System.AccessToken alike (the latter is the project build service).
        self._connection_data_url = f"{collection}/_apis/connectionData"
        self._identity: str | None = None
        self._identity_resolved = False
        self._pr_api = f"{self._repo_api}/pullRequests/{pr_number}"
        if bearer:
            authorization = f"Bearer {token}"
        else:
            authorization = "Basic " + base64.b64encode(f":{token}".encode()).decode()
        self._headers = {"Authorization": authorization, "Accept": "application/json"}
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        self._marker = marker("lgtmaybe", marker_key)
        self._describe_marker = marker("lgtmaybe-describe", marker_key)
        self._diagram_marker = marker(DIAGRAM_MARKER_FAMILY, marker_key)
        self._head_sha: str | None = None
        self._diff: str | None = None
        self._head_texts: dict[str, str] = {}
        self._scan_manifests = False
        # Changed files a side of which could not be fetched; named in the
        # summary so the review never reads as covering them.
        self._unread: list[str] = []
        self._resolve_fixed = resolve_fixed
        # Set by mark_reviewed on a complete run only; resolve-on-fix waits for it.
        self._reviewed_sha: str | None = None
        self._latest_iteration_id: int | None = None
        # Each iteration's head commit, so a thread's posting iteration can be
        # turned into the file text it was posted against.
        self._iteration_commits: dict[int, str] = {}
        # Each changed file's changeTrackingId, which ties a thread to the file
        # across iterations in Azure DevOps' own UI.
        self._tracking_ids: dict[str, int] = {}
        self._texts_at: dict[tuple[str, str], _Text] = {}

    # ------------------------------------------------------------------
    # ReviewGateway implementation
    # ------------------------------------------------------------------

    def get_pr_context(self) -> PRContext:
        """Fetch metadata and changed files, and assemble the unified diff.

        Read-only API calls throughout; nothing is checked out or executed.
        """
        meta = self._get_json(self._pr_api)
        iteration = self._latest_iteration()
        head_sha = (iteration.get("sourceRefCommit") or {}).get("commitId")
        base_sha = (iteration.get("commonRefCommit") or {}).get("commitId")
        if not head_sha:
            raise RuntimeError(
                f"Pull request {self._repo}!{self._pr_number} has no source commit on its "
                "latest iteration: unexpected Azure DevOps API response."
            )
        if not base_sha:
            # Never fall back to the target branch head: it carries changes
            # other merged pull requests made, so findings would anchor on lines
            # this one never touched.
            raise DiffUnavailable(
                "Azure DevOps reports no merge base (commonRefCommit) for this pull "
                "request's latest iteration, so its own changes cannot be told apart "
                "from the target branch's."
            )
        self._head_sha = head_sha
        iteration_id = iteration.get("id")
        self._latest_iteration_id = iteration_id if isinstance(iteration_id, int) else None

        changes = self._iteration_changes(iteration_id)
        texts = self._fetch_sides(changes, base_sha, head_sha)
        patches: list[str] = []
        file_contents: dict[str, str] = {}
        for change in changes:
            old = texts.get((change.old_path, base_sha), "") if change.old_path else ""
            new = texts.get((change.new_path, head_sha), "") if change.new_path else ""
            if (
                not is_reviewable(change.path)
                or isinstance(old, _Unreadable)
                or isinstance(new, _Unreadable)
            ):
                patches.append(
                    binary_file_patch(old_path=change.old_path, new_path=change.new_path)
                )
                continue
            # A side the change list promised but the Items API could not find
            # (a path renamed or deleted between the listing and the fetch) is
            # diffed as empty: every line then reads as added or removed. Name
            # the file so that rendering is never mistaken for the real change.
            for side, path, text, commit in (
                ("old", change.old_path, old, base_sha),
                ("new", change.new_path, new, head_sha),
            ):
                if path is not None and text is None:
                    _log.warning(
                        "the %s side of %r was not found at %s; diffing it as empty",
                        side,
                        path,
                        commit,
                    )
            patches.append(
                file_patch(
                    old_path=change.old_path,
                    new_path=change.new_path,
                    old_text=old or "",
                    new_text=new or "",
                )
            )
            if change.new_path is not None and isinstance(new, str):
                file_contents[change.new_path] = new
        self._diff = "".join(patches)
        self._head_texts = dict(file_contents)

        scan_contents = self._fetch_scan_contents(changes, head_sha) if self._scan_manifests else {}

        return PRContext(
            diff=self._diff,
            changed_files=[change.path for change in changes],
            base_sha=base_sha,
            head_sha=head_sha,
            repo=self._repo,
            pr_number=self._pr_number,
            file_contents=file_contents,
            scan_contents=scan_contents,
            title=meta.get("title") or "",
            description=meta.get("description") or "",
            commit_messages=self._fetch_commit_subjects(),
            head_branch=(meta.get("sourceRefName") or "").removeprefix("refs/heads/"),
            open_finding_threads=self.count_open_finding_threads(),
        )

    def post_review(
        self,
        findings: list[ReviewFinding],
        summary: str,
        diff: str | None = None,
    ) -> None:
        """Post each new finding as a positioned thread, then upsert the summary.

        Findings already carrying one of our hidden ids on an unresolved thread
        are skipped, so a re-run adds only what is new. A thread Azure DevOps
        rejects is demoted into the summary rather than lost. Last, our finding
        threads that are now fixed are resolved, after everything else posted.
        """
        if diff is None and findings:
            diff = self._diff if self._diff is not None else self.get_pr_context().diff

        commentable: CommentableLines = build_commentable_lines(diff or "")
        inline, demoted, broad = self._partition_findings(findings, commentable)
        threads = self._threads()

        if inline:
            inline = unposted(inline, self._existing_finding_keys(threads))
        for context, finding in inline:
            if not self._post_finding_thread(context, finding.path, render_inline_body(finding)):
                demoted.append(finding)

        body = (
            f"{summary}{render_demoted(demoted)}{render_broad(broad)}"
            f"{render_unread_files(self._unread)}\n\n{self._marker}"
        )
        self._upsert(body, self._marker, threads)
        if self._resolve_fixed:
            self._resolve_fixed_threads(findings, threads)

    def post_issue_comment(self, body: str) -> None:
        """Open a standalone general thread on the pull request."""
        self._create_thread(body, status=_NOTE_STATUS)

    # ------------------------------------------------------------------
    # Optional capabilities
    # ------------------------------------------------------------------

    def get_file_contents(self, path: str) -> str | None:
        """Head-revision text of one file, or None when it can't be read."""
        if self._head_sha is None:
            return None
        if path in self._head_texts:
            return self._head_texts[path]
        try:
            text = self._fetch_text(path, self._head_sha, _MAX_DIFF_BYTES)
        except Exception as exc:  # noqa: BLE001 (a missing file is not fatal)
            _log.debug("fetching a file failed: %s", exc)
            return None
        return text if isinstance(text, str) else None

    def post_describe_comment(self, body: str) -> None:
        """Upsert the structured description in its own marker family."""
        self._upsert(f"{body}\n\n{self._describe_marker}", self._describe_marker)

    def post_diagram_comment(self, body: str, *, completed_sha: str | None = None) -> None:
        """Upsert the change diagram in its own marker family.

        ``completed_sha`` is accepted and ignored: it drives incremental review,
        which this adapter does not offer.
        """
        self._upsert(f"{body}\n\n{self._diagram_marker}", self._diagram_marker)

    def list_conversation_comments(self) -> list[tuple[str, str]]:
        """Every person's comment on the pull request as ``(author, body)``.

        System comments (policy and vote events) are events, not anyone's words,
        so they are left out.
        """
        out: list[tuple[str, str]] = []
        for thread in self._threads():
            if thread.get("isDeleted"):
                continue
            for comment in thread.get("comments") or []:
                if comment.get("isDeleted") or comment.get("commentType", "text") not in (
                    "text",
                    1,
                ):
                    continue
                author = (comment.get("author") or {}).get("displayName") or ""
                out.append((author, comment.get("content") or ""))
        return out

    def set_scan_manifests(self, enabled: bool) -> None:
        """Also fetch dependency-manifest text on the next context fetch."""
        self._scan_manifests = enabled

    def mark_reviewed(self, head_sha: str | None) -> None:
        """Record that this run reviewed ``head_sha`` completely (None: it did not).

        No watermark is written, since incremental review is not offered. It is
        the gate for resolve-on-fix: a partial run or a failure notice is missing
        findings, not reporting them fixed, so neither may resolve a thread.
        """
        self._reviewed_sha = head_sha

    def reply_in_thread(self, thread_id: str, body: str) -> None:
        """Reply to the comment that opened an existing thread."""
        resp = self._client.post(
            f"{self._pr_api}/threads/{thread_id}/comments",
            headers=self._headers,
            params={"api-version": _API_VERSION},
            json={"parentCommentId": _ROOT_COMMENT_ID, "content": body, "commentType": "text"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    def count_open_finding_threads(self) -> int:
        """How many of our finding threads are still open. Never fails the review."""
        try:
            return len(self._open_finding_threads(self._threads()))
        except Exception as exc:  # noqa: BLE001 (disclosure metadata only)
            _log.warning("counting open finding threads failed: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Internals: context
    # ------------------------------------------------------------------

    def _latest_iteration(self) -> dict[str, Any]:
        """The newest iteration: what the pull request currently proposes."""
        iterations = self._get_json(f"{self._pr_api}/iterations").get("value") or []
        if not iterations:
            raise RuntimeError(
                f"Pull request {self._repo}!{self._pr_number} has no iterations: "
                "unexpected Azure DevOps API response."
            )
        self._iteration_commits = {
            it["id"]: commit
            for it in iterations
            if isinstance(it.get("id"), int)
            and isinstance(commit := (it.get("sourceRefCommit") or {}).get("commitId"), str)
        }
        latest: dict[str, Any] = max(iterations, key=lambda it: int(it.get("id") or 0))
        return latest

    def _iteration_changes(self, iteration_id: Any) -> list[_Change]:
        """The iteration's changed files, compared against the merge base.

        ``$compareTo=0`` is the merge base, which is what GitHub's three-dot pull
        request diff shows too. One page, one past the cap: a longer list is
        refused as a whole rather than reviewed in part without saying so.
        """
        payload = self._get_json(
            f"{self._pr_api}/iterations/{iteration_id}/changes",
            params={"$top": _MAX_DIFF_FILES + 1, "$compareTo": 0},
        )
        entries = payload.get("changeEntries") or []
        if payload.get("nextSkip") or len(entries) > _MAX_DIFF_FILES:
            raise DiffUnavailable(
                f"this pull request changes more than {_MAX_DIFF_FILES} files. Azure DevOps "
                "serves no unified diff, so lgtmaybe assembles one file by file, and stops "
                "short of a change this large rather than review part of it."
            )
        changes: list[_Change] = []
        self._tracking_ids = {}
        for entry in entries:
            change = _describe_change(entry)
            if change is not None:
                changes.append(change)
                tracking_id = entry.get("changeTrackingId")
                if isinstance(tracking_id, int):
                    self._tracking_ids[change.path] = tracking_id
        return changes

    def _fetch_sides(
        self, changes: list[_Change], base_sha: str, head_sha: str
    ) -> dict[tuple[str, str], _Text]:
        """Both sides of every reviewable file, fetched concurrently.

        A side that still fails after its retries degrades only its own file:
        it renders as unreadable (never a fabricated diff), is logged with its
        path, side and reason, and is named in the summary. An auth failure is
        not a per-file problem and propagates.
        """
        wanted: list[tuple[str, str, str]] = []
        self._unread = []
        for change in changes:
            if not is_reviewable(change.path):
                continue  # named in the diff, never fetched
            if change.old_path is not None:
                wanted.append((change.old_path, base_sha, "old"))
            if change.new_path is not None:
                wanted.append((change.new_path, head_sha, "new"))
        owner = {
            (path, commit): change.path
            for change in changes
            for path, commit in ((change.old_path, base_sha), (change.new_path, head_sha))
            if path is not None
        }

        def fetch(key: tuple[str, str, str]) -> _Text:
            path, commit, side = key
            try:
                return self._fetch_text(path, commit, _MAX_DIFF_BYTES)
            except _FetchFailed as exc:
                _log.warning(
                    "the %s side of %r could not be fetched (%s); not reviewing it",
                    side,
                    path,
                    exc.reason,
                )
                self._unread.append(owner[path, commit])
                return _Unreadable.blob

        with ThreadPoolExecutor(max_workers=_CONTENT_FETCH_WORKERS) as pool:
            fetched = list(pool.map(fetch, wanted))
        # Concurrent appends land in completion order; report in change order.
        order = {change.path: i for i, change in enumerate(changes)}
        self._unread = sorted(set(self._unread), key=order.__getitem__)
        return {
            (path, commit): text for (path, commit, _), text in zip(wanted, fetched, strict=True)
        }

    def _fetch_scan_contents(self, changes: list[_Change], head_sha: str) -> dict[str, str]:
        """Head text of changed dependency manifests, within two byte ceilings.

        Each manifest may be up to ``_MAX_SCAN_BYTES``, and all of them
        together up to ``_MAX_SCAN_TOTAL_BYTES``; a manifest past either is
        skipped and logged. Fetched one at a time so the total stays bounded
        while it is read. Scanning is supplementary, so a manifest that cannot
        be fetched is skipped rather than failing the review.
        """
        remaining = _MAX_SCAN_TOTAL_BYTES
        out: dict[str, str] = {}
        for change in changes:
            path = change.new_path
            if path is None or not is_scannable_manifest(path):
                continue
            limit = min(_MAX_SCAN_BYTES, remaining)
            try:
                text = self._fetch_text(path, head_sha, limit) if limit > 0 else _Unreadable.blob
            except _FetchFailed as exc:
                _log.warning("skipping manifest %r for scanning (%s)", path, exc.reason)
                continue
            if isinstance(text, str):
                out[path] = text
                remaining -= len(text.encode("utf-8"))
            elif text is not None:
                _log.warning(
                    "skipping manifest %r for scanning: binary, or past the %d-byte ceiling",
                    path,
                    limit,
                )
        return out

    def _fetch_text(self, path: str, commit: str, limit: int) -> _Text:
        """One file's text at ``commit``: None when absent, ``_Unreadable`` when
        binary or over ``limit`` bytes (the body is streamed and abandoned there,
        so an oversized file never lands in memory).

        429, 5xx, timeouts and dropped connections are retried up to ``_FETCH_ATTEMPTS`` times and
        then raise ``_FetchFailed``. 401/403 raise ``httpx.HTTPStatusError``
        straight away: Azure Repos has no per-file read permission, so either
        means the token itself is wrong, and the review must fail loudly.
        """
        reason = "no attempt made"
        for attempt in range(1, _FETCH_ATTEMPTS + 1):
            delay = _RETRY_BACKOFF * attempt
            try:
                return self._fetch_text_once(path, commit, limit)
            except httpx.TransportError as exc:  # a timeout, or a dropped connection
                reason = (
                    "timed out"
                    if isinstance(exc, httpx.TimeoutException)
                    else f"transport error ({type(exc).__name__})"
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    raise
                reason = f"HTTP {status}"
                if status != 429 and status < 500:
                    raise _FetchFailed(reason) from exc
                delay = _retry_after(exc.response, delay)
            if attempt < _FETCH_ATTEMPTS:
                _sleep(delay)
        raise _FetchFailed(f"{reason} after {_FETCH_ATTEMPTS} attempts")

    def _fetch_text_once(self, path: str, commit: str, limit: int) -> _Text:
        params = {
            "api-version": _API_VERSION,
            "path": f"/{path}",
            "versionDescriptor.version": commit,
            "versionDescriptor.versionType": "commit",
        }
        headers = {**self._headers, "Accept": "application/octet-stream"}
        with self._client.stream(
            "GET", f"{self._repo_api}/items", headers=headers, params=params, timeout=_TIMEOUT
        ) as resp:
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = bytearray()
            for chunk in resp.iter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    return _Unreadable.blob
        if b"\x00" in data:
            return _Unreadable.blob
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return _Unreadable.blob

    def _fetch_commit_subjects(self) -> list[str]:
        """Commit subject lines, feeding the intent lens. Never fails the review."""
        try:
            commits = self._get_json(f"{self._pr_api}/commits").get("value") or []
            return [
                message.splitlines()[0]
                for item in commits
                if (message := (item.get("comment") or "").strip())
            ]
        except Exception as exc:  # noqa: BLE001 (intent is a nice-to-have)
            _log.warning("fetching commit subjects failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internals: posting
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
        """Split findings into positioned threads, body-demoted, and broad.

        The placement rule is shared (``core.comment.partition_findings``); only
        the payload here is Azure DevOps': the RIGHT/LEFT side becomes a
        right-file or left-file range on one line.
        """

        def place(f: ReviewFinding) -> dict[str, Any]:
            side = "left" if f.side == "LEFT" else "right"
            return {
                "filePath": f"/{f.path}",
                f"{side}FileStart": {"line": f.line, "offset": 1},
                f"{side}FileEnd": {"line": f.line, "offset": 1},
            }

        return partition_findings(findings, commentable, place)

    def _post_finding_thread(self, context: dict[str, Any], path: str, body: str) -> bool:
        """Open one positioned finding thread; False when Azure DevOps refuses it.

        Best-effort per finding, so one unplaceable finding never takes the rest
        of the review down with it. A 401/403 is not about one finding: the token
        cannot write threads at all, so it fails loudly like the fetch and upsert
        paths rather than burying every finding in the summary.

        The thread records the iteration it was posted at, which resolve-on-fix
        needs later. That record is a nice-to-have, so a thread refused with 400
        (the host rejecting the payload) is tried once more without it before
        the finding is demoted. Nothing else is retried: after a timeout or a
        5xx the host may already have created the thread, and a second attempt
        would duplicate it.
        """
        tracking = self._tracking_context(path)
        for attempt in (tracking, None) if tracking is not None else (None,):
            try:
                self._create_thread(body, status=_FINDING_STATUS, context=context, tracking=attempt)
                return True
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    raise
                _log.warning("posting a finding thread failed: %s", exc)
                if exc.response.status_code != 400:
                    return False
            except Exception as exc:  # noqa: BLE001 (one rejected position is not fatal)
                _log.warning("posting a finding thread failed: %s", exc)
                return False
        return False

    def _tracking_context(self, path: str) -> dict[str, Any] | None:
        """The ``pullRequestThreadContext`` tying a new thread to this iteration.

        Iteration 1 to the latest is the "all changes" comparison, which is the
        merge-base diff the findings were made against. None before the context
        has been fetched: the thread is then posted untracked, and never resolved.
        """
        if self._latest_iteration_id is None:
            return None
        tracking: dict[str, Any] = {
            "iterationContext": {
                "firstComparingIteration": 1,
                "secondComparingIteration": self._latest_iteration_id,
            }
        }
        if (tracking_id := self._tracking_ids.get(path)) is not None:
            tracking["changeTrackingId"] = tracking_id
        return tracking

    def _create_thread(
        self,
        content: str,
        *,
        status: str,
        context: dict[str, Any] | None = None,
        tracking: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": content, "commentType": "text"}],
            "status": status,
        }
        if context is not None:
            payload["threadContext"] = context
        if tracking is not None:
            payload["pullRequestThreadContext"] = tracking
        resp = self._client.post(
            f"{self._pr_api}/threads",
            headers=self._headers,
            params={"api-version": _API_VERSION},
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    def _upsert(self, body: str, family: str, threads: list[dict[str, Any]] | None = None) -> None:
        """Edit our earlier comment in this marker family, or open a new thread.

        Only threads the token's own identity opened are candidates, so a
        marker someone else planted is never an edit target. When the identity
        is unknown every marked thread is a candidate: Azure DevOps lets only a
        comment's author edit it, so another author's comment answers 403 and
        the next is tried.

        A 403 or 404 on an edit moves on to the next candidate, and when none
        is left a new thread is opened. Any other failed edit raises: that is
        deliberate, so a token that has lost its rights (401) or a host fault
        surfaces as a failed review instead of a quietly duplicated summary.
        """
        candidates = _find_root_comments(self._threads() if threads is None else threads, family)
        identity = self._own_identity()
        if identity is not None:
            candidates = [(tid, root) for tid, root in candidates if _author_id(root) == identity]
        for thread_id, root in candidates:
            comment_id = root.get("id")
            resp = self._client.patch(
                f"{self._pr_api}/threads/{thread_id}/comments/{comment_id}",
                headers=self._headers,
                params={"api-version": _API_VERSION},
                json={"content": body},
                timeout=_TIMEOUT,
            )
            if resp.is_success:
                return
            if resp.status_code not in (403, 404):
                resp.raise_for_status()
            _log.warning("editing an earlier comment failed (HTTP %s)", resp.status_code)
        self._create_thread(body, status=_NOTE_STATUS)

    def _existing_finding_keys(self, threads: list[dict[str, Any]]) -> list[set[str]]:
        """Hidden ids of our own unresolved finding threads, one set per thread.

        Only a thread whose first comment the token's own identity wrote counts.
        A finding's ids derive from its path and title, both public, so anyone
        who may comment on the pull request (its author included) could plant a
        thread carrying them and suppress the finding. When the identity cannot
        be resolved nothing is trusted: a finding may then post twice, but is
        never hidden.
        """
        identity = self._own_identity()
        if identity is None:
            return []
        keys: list[set[str]] = []
        for thread in threads:
            if thread.get("isDeleted") or thread.get("status") in _FIXED_STATUSES:
                continue
            root = _root_comment(thread)
            if root is None or _author_id(root) != identity:
                continue
            if found := finding_keys(root.get("content") or ""):
                keys.append(found)
        return keys

    # ------------------------------------------------------------------
    # Internals: resolve-on-fix
    # ------------------------------------------------------------------

    def _resolve_fixed_threads(
        self, findings: list[ReviewFinding], threads: list[dict[str, Any]]
    ) -> None:
        """Resolve, then reply in, each of our finding threads that is now fixed.

        Only after a complete run of the head this adapter fetched. Resolving
        comes before the reply, so a refused resolve leaves no "Looks resolved."
        on a thread that stays open. A 401 or 403 is the identity's, not the
        thread's, so the first one stops the rest. Never fails the review.
        """
        if self._reviewed_sha is None or self._reviewed_sha != self._head_sha:
            return
        try:
            fixed = self._fixed_thread_ids(findings, threads)
        except Exception as exc:  # noqa: BLE001 (resolving never fails a review)
            _log.warning("finding fixed threads failed: %s", exc)
            return
        for thread_id in fixed:
            try:
                resp = self._client.patch(
                    f"{self._pr_api}/threads/{thread_id}",
                    headers=self._headers,
                    params={"api-version": _API_VERSION},
                    json={"status": "fixed"},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    _log.warning(
                        "resolving threads is not permitted for this identity, so they stay "
                        "open. Grant it 'Contribute to pull requests', or set "
                        "resolve_fixed: false to stop trying. (%s)",
                        exc,
                    )
                    return
                _log.warning("resolving thread %s failed: %s", thread_id, exc)
                continue
            except Exception as exc:  # noqa: BLE001 (one thread never blocks the rest)
                _log.warning("resolving thread %s failed: %s", thread_id, exc)
                continue
            try:
                self.reply_in_thread(thread_id, _RESOLVED_REPLY)
            except Exception as exc:  # noqa: BLE001 (nothing depends on the reply)
                _log.warning("replying in resolved thread %s failed: %s", thread_id, exc)

    def _fixed_thread_ids(
        self, findings: list[ReviewFinding], threads: list[dict[str, Any]]
    ) -> list[str]:
        """Our open finding threads whose finding is gone and whose line changed.

        Gone means neither hidden id was produced this run, so a reworded finding
        still counts as reported. The line check is only paid for gone findings.
        """
        current = current_finding_keys(findings)
        return [
            str(thread.get("id"))
            for thread, root in self._open_finding_threads(threads)
            if not finding_keys(root.get("content") or "") & current and self._is_outdated(thread)
        ]

    def _open_finding_threads(
        self, threads: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """``(thread, root comment)`` of each open finding thread our identity opened.

        Hidden ids are public, so a thread anyone else opened is never ours to
        resolve; with the identity unknown, none is.
        """
        identity = self._own_identity()
        if identity is None:
            return []
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for thread in threads:
            if thread.get("isDeleted") or thread.get("status") not in _OPEN_STATUSES:
                continue
            root = _root_comment(thread)
            if root is None or _author_id(root) != identity:
                continue
            if finding_keys(root.get("content") or ""):
                out.append((thread, root))
        return out

    def _is_outdated(self, thread: dict[str, Any]) -> bool:
        """Whether the line a finding thread was posted on has changed since.

        The thread's posting iteration names the commit it was placed against;
        the file is read there and at our head, and the line is outdated when the
        change between them replaced or removed it, or the file is gone. Once
        Azure DevOps has re-tracked a thread onto a later iteration, its
        ``trackingCriteria`` keeps the original position, so that wins.

        Fails closed: no recorded iteration, a left-side thread (pushes do not
        change the base), an unreadable side, or any error all read as not
        outdated, so nothing is resolved without evidence.
        """
        try:
            pr_context = thread.get("pullRequestThreadContext") or {}
            original = pr_context.get("trackingCriteria") or {}
            context = thread.get("threadContext") or {}
            iteration = original.get("secondComparingIteration") or (
                pr_context.get("iterationContext") or {}
            ).get("secondComparingIteration")
            start = original.get("origRightFileStart") or context.get("rightFileStart") or {}
            path = _repo_path(original.get("origFilePath") or context.get("filePath"))
            line = start.get("line")
            posted_at = (
                self._iteration_commits.get(iteration) if isinstance(iteration, int) else None
            )
            head = self._head_sha
            if not head or not posted_at or posted_at == head or not path:
                return False
            if not isinstance(line, int) or line < 1:
                return False
            before = self._text_at(path, posted_at)
            if not isinstance(before, str):
                return False
            after = self._text_at(path, head)
            if after is None:
                return True  # the file is gone from the head
            if not isinstance(after, str):
                return False
            old_lines = before.split("\n")
            if line > len(old_lines):
                return False
            matcher = SequenceMatcher(None, old_lines, after.split("\n"), autojunk=False)
            return any(
                tag in ("replace", "delete") and i1 < line <= i2
                for tag, i1, i2, _j1, _j2 in matcher.get_opcodes()
            )
        except Exception as exc:  # noqa: BLE001 (fail closed: leave the thread open)
            _log.warning("telling whether thread %s is outdated failed: %s", thread.get("id"), exc)
            return False

    def _text_at(self, path: str, commit: str) -> _Text:
        """A file's text at ``commit``, read once per run."""
        if commit == self._head_sha and path in self._head_texts:
            return self._head_texts[path]
        key = (path, commit)
        if key not in self._texts_at:
            self._texts_at[key] = self._fetch_text(path, commit, _MAX_DIFF_BYTES)
        return self._texts_at[key]

    def _own_identity(self) -> str | None:
        """The id of the identity the token authenticates as, resolved once.

        None when it cannot be read; callers must then treat no thread as ours.
        """
        if not self._identity_resolved:
            self._identity_resolved = True
            try:
                resp = self._client.get(
                    self._connection_data_url, headers=self._headers, timeout=_TIMEOUT
                )
                resp.raise_for_status()
                user = (resp.json() or {}).get("authenticatedUser") or {}
                identity = user.get("id")
                self._identity = identity if isinstance(identity, str) and identity else None
            except Exception as exc:  # noqa: BLE001 (fail safe: trust no thread)
                _log.warning("reading the token's identity failed: %s", exc)
            if self._identity is None:
                _log.warning(
                    "the token's identity is unknown, so no earlier thread is trusted as ours"
                )
        return self._identity

    def _threads(self) -> list[dict[str, Any]]:
        """Every thread on the pull request (the endpoint is not paged)."""
        threads = self._get_json(f"{self._pr_api}/threads").get("value") or []
        return [thread for thread in threads if isinstance(thread, dict)]

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._client.get(
            url,
            headers=self._headers,
            params={"api-version": _API_VERSION, **(params or {})},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}


def _retry_after(response: httpx.Response, default: float) -> float:
    """Seconds a throttled response asks us to wait, capped; ``default`` if unsaid."""
    try:
        seconds = float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return default
    return max(0.0, min(seconds, _MAX_RETRY_AFTER))


def _segment(value: str) -> str:
    """One URL path segment, percent-encoded (project names may hold spaces)."""
    return quote(value, safe="")


def _root_comment(thread: dict[str, Any]) -> dict[str, Any] | None:
    """The comment that opened a thread, unless it was deleted."""
    comments = thread.get("comments") or []
    if not comments or not isinstance(comments[0], dict) or comments[0].get("isDeleted"):
        return None
    root: dict[str, Any] = comments[0]
    return root


def _find_root_comments(
    threads: list[dict[str, Any]], family: str
) -> list[tuple[Any, dict[str, Any]]]:
    """``(thread id, root comment)`` of every live thread opened with ``family``'s marker."""
    found: list[tuple[Any, dict[str, Any]]] = []
    for thread in threads:
        if thread.get("isDeleted"):
            continue
        root = _root_comment(thread)
        if root is not None and family in (root.get("content") or ""):
            found.append((thread.get("id"), root))
    return found


def _author_id(comment: dict[str, Any]) -> str | None:
    """The identity id of a comment's author, or None when absent."""
    author = comment.get("author")
    identity = author.get("id") if isinstance(author, dict) else None
    return identity if isinstance(identity, str) and identity else None


def _change_kinds(change_type: Any) -> set[str]:
    """The flags of a change type, from its string or integer serialisation."""
    if isinstance(change_type, int):
        return {name for bit, name in _CHANGE_FLAGS.items() if change_type & bit}
    return {part.strip().lower() for part in str(change_type or "").split(",") if part.strip()}


def _repo_path(raw: Any) -> str | None:
    """A repository path without its leading slash, or None when unusable."""
    if not isinstance(raw, str):
        return None
    path = raw.lstrip("/")
    return path or None


def _describe_change(entry: dict[str, Any]) -> _Change | None:
    """One change entry as old/new paths, or None for folders and unsafe paths.

    A path carrying a control character is dropped: it cannot be written into
    a diff header without letting the filename (chosen by the PR author) forge
    header lines of its own.
    """
    item = entry.get("item") or {}
    if item.get("isFolder") or item.get("gitObjectType") == "tree":
        return None
    path = _repo_path(item.get("path"))
    original = _repo_path(entry.get("originalPath"))
    if path is None:
        return None
    if has_unsafe_path_chars(path) or (original is not None and has_unsafe_path_chars(original)):
        _log.warning("skipping a changed file whose path holds control characters: %r", path)
        return None
    kinds = _change_kinds(entry.get("changeType"))
    if "delete" in kinds:
        return _Change(old_path=path, new_path=None)
    if "add" in kinds or "undelete" in kinds:
        return _Change(old_path=None, new_path=path)
    if "rename" in kinds and original is not None and original != path:
        return _Change(old_path=original, new_path=path)
    # Anything else, including a rename whose originalPath is missing or names
    # the same path, is an edit in place. Without a distinct old path there is
    # no honest old side to read, and reading the new path at the merge base is
    # the closest truthful comparison; if that path did not exist there, the
    # 404 is logged by get_pr_context rather than papered over.
    return _Change(old_path=path, new_path=path)
