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

Deliberately absent, and not claimed: incremental review, thread resolution,
labels, commit statuses, 👎 feedback, and base-branch checkout. The Azure DevOps
API could serve most of them; they are "not yet", and the CLI runs a full
review without them.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx

from lgtmaybe.core.comment import (
    DIAGRAM_MARKER_FAMILY,
    current_finding_keys,
    finding_keys,
    marker,
    render_broad,
    render_demoted,
    render_inline_body,
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

# Finding threads are left open for the author to resolve; every other thread
# (summary, description, notices) is created closed, so it never holds a merge
# behind a "resolve all comments" branch policy.
_FINDING_STATUS = "active"
_NOTE_STATUS = "closed"

# A resolved finding that comes back is reported again (as on GitLab); a thread
# the author marked won't-fix, by-design, or closed stays dismissed.
_FIXED_STATUSES = frozenset({"fixed", 2})

# Change types arrive as a comma-separated flag string ("rename, edit"), or as
# the integer flags they serialise from.
_CHANGE_FLAGS = {1: "add", 2: "edit", 8: "rename", 16: "delete", 32: "undelete"}


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
        base_sha = (iteration.get("commonRefCommit") or {}).get("commitId") or (
            iteration.get("targetRefCommit") or {}
        ).get("commitId")
        if not base_sha or not head_sha:
            raise RuntimeError(
                f"Pull request {self._repo}!{self._pr_number} has no source or merge-base "
                "commit on its latest iteration: unexpected Azure DevOps API response."
            )
        self._head_sha = head_sha

        changes = self._iteration_changes(iteration.get("id"))
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

        scan_contents: dict[str, str] = {}
        if self._scan_manifests:
            manifests = [
                c.new_path
                for c in changes
                if c.new_path is not None and is_scannable_manifest(c.new_path)
            ]
            with ThreadPoolExecutor(max_workers=_CONTENT_FETCH_WORKERS) as pool:
                fetched = pool.map(
                    lambda p: (p, self._fetch_text(p, head_sha, _MAX_SCAN_BYTES)), manifests
                )
                scan_contents = {path: text for path, text in fetched if isinstance(text, str)}

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
        rejects is demoted into the summary rather than lost.
        """
        if diff is None and findings:
            diff = self._diff if self._diff is not None else self.get_pr_context().diff

        commentable: CommentableLines = build_commentable_lines(diff or "")
        inline, demoted, broad = self._partition_findings(findings, commentable)
        threads = self._threads()

        if inline:
            unmatched = self._existing_finding_keys(threads)
            new_inline: list[tuple[dict[str, Any], ReviewFinding]] = []
            for context, finding in inline:
                keys = current_finding_keys([finding])
                already = next((i for i, existing in enumerate(unmatched) if existing & keys), None)
                if already is None:
                    new_inline.append((context, finding))
                else:
                    unmatched.pop(already)
            inline = new_inline
        for context, finding in inline:
            if not self._post_finding_thread(context, render_inline_body(finding)):
                demoted.append(finding)

        body = f"{summary}{render_demoted(demoted)}{render_broad(broad)}\n\n{self._marker}"
        self._upsert(body, self._marker, threads)

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
        for entry in entries:
            change = _describe_change(entry)
            if change is not None:
                changes.append(change)
        return changes

    def _fetch_sides(
        self, changes: list[_Change], base_sha: str, head_sha: str
    ) -> dict[tuple[str, str], _Text]:
        """Both sides of every reviewable file, fetched concurrently."""
        wanted: list[tuple[str, str]] = []
        for change in changes:
            if not is_reviewable(change.path):
                continue  # named in the diff, never fetched
            if change.old_path is not None:
                wanted.append((change.old_path, base_sha))
            if change.new_path is not None:
                wanted.append((change.new_path, head_sha))
        with ThreadPoolExecutor(max_workers=_CONTENT_FETCH_WORKERS) as pool:
            fetched = pool.map(
                lambda key: self._fetch_text(key[0], key[1], _MAX_DIFF_BYTES), wanted
            )
            return dict(zip(wanted, fetched, strict=True))

    def _fetch_text(self, path: str, commit: str, limit: int) -> _Text:
        """One file's text at ``commit``: None when absent, ``_Unreadable`` when
        binary or over ``limit`` bytes (the body is streamed and abandoned there,
        so an oversized file never lands in memory)."""
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

        Same rule as every adapter: a finding goes inline only when it is
        confidently anchored AND lands on a real commentable diff line. The
        RIGHT/LEFT side becomes a right-file or left-file range on one line.
        """
        inline: list[tuple[dict[str, Any], ReviewFinding]] = []
        demoted: list[ReviewFinding] = []
        broad: list[ReviewFinding] = []
        for f in findings:
            if f.broad:
                broad.append(f)
                continue
            if not f.anchored or (f.path, f.line, f.side) not in commentable:
                demoted.append(f)
                continue
            side = "left" if f.side == "LEFT" else "right"
            context: dict[str, Any] = {
                "filePath": f"/{f.path}",
                f"{side}FileStart": {"line": f.line, "offset": 1},
                f"{side}FileEnd": {"line": f.line, "offset": 1},
            }
            inline.append((context, f))
        return inline, demoted, broad

    def _post_finding_thread(self, context: dict[str, Any], body: str) -> bool:
        """Open one positioned finding thread; False when Azure DevOps refuses it.

        Best-effort per finding, so one unplaceable finding never takes the rest
        of the review down with it.
        """
        try:
            self._create_thread(body, status=_FINDING_STATUS, context=context)
            return True
        except Exception as exc:  # noqa: BLE001 (one rejected position is not fatal)
            _log.warning("posting a finding thread failed: %s", exc)
            return False

    def _create_thread(
        self, content: str, *, status: str, context: dict[str, Any] | None = None
    ) -> None:
        payload: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": content, "commentType": "text"}],
            "status": status,
        }
        if context is not None:
            payload["threadContext"] = context
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

        Only a comment's author may edit it, so a comment someone else posted
        carrying our marker answers 403. Each candidate is tried in turn, so a
        planted marker neither fails the review nor hides our own comment
        behind it; with none editable, a new thread is opened.
        """
        candidates = _find_root_comments(self._threads() if threads is None else threads, family)
        for thread_id, comment_id in candidates:
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
        """Hidden ids of our unresolved finding threads, one set per thread."""
        keys: list[set[str]] = []
        for thread in threads:
            if thread.get("isDeleted") or thread.get("status") in _FIXED_STATUSES:
                continue
            root = _root_comment(thread)
            if root is not None and (found := finding_keys(root.get("content") or "")):
                keys.append(found)
        return keys

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


def _find_root_comments(threads: list[dict[str, Any]], family: str) -> list[tuple[Any, Any]]:
    """``(thread id, comment id)`` of every live thread opened with ``family``'s marker."""
    found: list[tuple[Any, Any]] = []
    for thread in threads:
        if thread.get("isDeleted"):
            continue
        root = _root_comment(thread)
        if root is not None and family in (root.get("content") or ""):
            found.append((thread.get("id"), root.get("id")))
    return found


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
    return _Change(old_path=path, new_path=path)
