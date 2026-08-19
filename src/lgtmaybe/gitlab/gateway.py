"""GitLab REST adapter.

Of the three forges lgtmaybe posts to, GitLab is the one whose model genuinely
differs rather than merely renaming things:

1. **No batched review object.** GitHub and Gitea both submit one review
   carrying every inline comment; GitLab has no such thing. Each finding is its
   own *discussion*, posted individually, and the summary is an ordinary note.
2. **Positions carry the diff refs.** A discussion is anchored by a ``position``
   object naming ``old_path``/``new_path``, ``old_line``/``new_line``, **and**
   the merge request's ``base_sha``/``start_sha``/``head_sha``. lgtmaybe keeps
   GitHub's RIGHT/LEFT vocabulary internally and translates here.
3. **Threads resolve over plain REST.** Resolving a GitHub thread needs GraphQL;
   on GitLab it is a ``PUT`` on the discussion, which is why this adapter can
   offer resolve-on-fix where the Gitea one cannot.

Deliberately absent: incremental review. GitLab's compare endpoint returns
per-file diffs that could be reassembled into a unified diff, so this is a
"not yet" rather than a "cannot" — but until it is built and measured, the
capability is not claimed and every run is a full review.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import httpx

from lgtmaybe.core.comment import (
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
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import ActiveFinding, PRContext, ReviewFinding

_log = get_logger(__name__)

_TIMEOUT = 30.0
_CONTENT_FETCH_WORKERS = 8

# GitLab's list endpoints page at 20 by default and cap at 100.
_PAGE_LIMIT = 100

# Written into a resolved thread before it is closed, so the trail explains
# itself to whoever reads the merge request later.
_RESOLVED_REPLY = "✅ Looks resolved."

# GitLab commit statuses are its check-run equivalent; its state vocabulary is
# narrower than GitHub's conclusions, so map rather than pass through.
_STATUS_STATES = {
    "success": "success",
    "neutral": "success",
    "skipped": "success",
    "failure": "failed",
    "action_required": "failed",
    "cancelled": "canceled",
    "timed_out": "failed",
}


class GitLabGateway:
    """GitLab REST adapter.

    Args:
        host:      GitLab hostname, e.g. "gitlab.com" or a self-hosted instance.
        repo:      Full project path, e.g. "group/subgroup/project".
        pr_number: Merge request *iid* (the per-project number in the URL).
        token:     GitLab access token (project, group, or personal).
        client:    Injected httpx.Client; a default is created if omitted.
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
    ) -> None:
        self._repo = repo
        self._pr_number = pr_number
        # GitLab addresses a project by its URL-encoded full path, which is what
        # lets an arbitrarily nested group path travel in a URL segment.
        self._project = quote(repo, safe="")
        self._api = f"{scheme}://{host}/api/v4/projects/{self._project}"
        self._mr_api = f"{self._api}/merge_requests/{pr_number}"
        self._headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        self._marker = marker("lgtmaybe", marker_key)
        self._describe_marker = marker("lgtmaybe-describe", marker_key)
        self._diagram_marker = marker("lgtmaybe-diagram", marker_key)
        self._head_sha: str | None = None
        # The three SHAs every positioned discussion must carry. Cached from the
        # MR payload, because posting a finding needs them and post_review is
        # reachable without a preceding get_pr_context (a failure notice).
        self._diff_refs: dict[str, str] | None = None
        self._scan_manifests = False
        self._active_findings: list[ActiveFinding] | None = None
        self._validated_fixed_thread_ids: set[str] | None = None

    # ------------------------------------------------------------------
    # ReviewGateway implementation
    # ------------------------------------------------------------------

    def get_pr_context(self) -> PRContext:
        """Fetch MR metadata, unified diff, changed files, and head file text."""
        meta = self._get_json(self._mr_api)
        refs = meta.get("diff_refs") or {}
        base_sha = refs.get("base_sha")
        head_sha = refs.get("head_sha")
        if not base_sha or not head_sha:
            raise RuntimeError(
                f"Merge request metadata for {self._repo}!{self._pr_number} is missing the "
                "base/head SHA — unexpected GitLab API response."
            )
        self._head_sha = head_sha
        self._diff_refs = {
            "base_sha": base_sha,
            "head_sha": head_sha,
            # start_sha only differs from base_sha on a rebased MR; falling back
            # keeps a position valid rather than rejecting it outright.
            "start_sha": refs.get("start_sha") or base_sha,
        }

        diff = self._fetch_mr_diff()
        changed_files = [
            item.get("new_path") or item.get("old_path") or ""
            for item in self._paginate(f"{self._mr_api}/diffs")
        ]
        changed_files = [path for path in changed_files if path]

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
            description=meta.get("description") or "",
            commit_messages=self._fetch_commit_subjects(),
            head_branch=meta.get("source_branch") or "",
            open_finding_threads=self.count_open_finding_threads(),
        )

    def post_review(
        self,
        findings: list[ReviewFinding],
        summary: str,
        diff: str | None = None,
    ) -> None:
        """Post each finding as its own discussion, then upsert the summary note.

        Findings already carrying one of our hidden ids on the MR are skipped, so
        a re-run adds only what is new. Threads the caller validated as fixed are
        replied to and resolved afterwards — never before, so a failure part-way
        through leaves the record honest rather than closing a finding that was
        never re-stated.
        """
        if diff is None and findings:
            diff = self._fetch_mr_diff()

        commentable: CommentableLines = build_commentable_lines(diff or "")
        inline, demoted, broad = self._partition_findings(findings, commentable)

        if inline:
            already = self._existing_finding_keys()
            inline = [
                (position, f) for position, f in inline if not (current_finding_keys([f]) & already)
            ]
        for position, finding in inline:
            self._post_discussion(position, render_inline_body(finding))

        body = f"{summary}{render_demoted(demoted)}{render_broad(broad)}\n\n{self._marker}"
        self._upsert_note(body, self._marker)
        self._resolve_fixed_threads()

    def post_issue_comment(self, body: str) -> None:
        """Post a standalone note to the merge request conversation."""
        resp = self._client.post(
            f"{self._mr_api}/notes", headers=self._headers, json={"body": body}, timeout=_TIMEOUT
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
        self._upsert_note(f"{body}\n\n{self._describe_marker}", self._describe_marker)

    def post_diagram_comment(self, body: str, *, completed_sha: str | None = None) -> None:
        """Upsert the change diagram in its own marker family.

        ``completed_sha`` is accepted and ignored: it drives incremental review,
        which this adapter does not offer.
        """
        self._upsert_note(f"{body}\n\n{self._diagram_marker}", self._diagram_marker)

    def set_scan_manifests(self, enabled: bool) -> None:
        """Also fetch dependency-manifest text on the next context fetch."""
        self._scan_manifests = enabled

    def list_active_findings(self) -> list[ActiveFinding]:
        """Our own unresolved finding discussions, keyed by their hidden ids."""
        active: list[ActiveFinding] = []
        for discussion in self._discussions():
            notes = discussion.get("notes") or []
            if not notes:
                continue
            first = notes[0]
            if first.get("resolved"):
                continue
            body = first.get("body") or ""
            keys = finding_keys(body)
            if not keys:
                continue  # someone else's discussion
            fingerprints = _marker_values(body, "lgtmaybe-finding")
            identities = _marker_values(body, "lgtmaybe-identity")
            active.append(
                ActiveFinding(
                    thread_id=str(discussion.get("id")),
                    comment_id=first.get("id"),
                    path=(first.get("position") or {}).get("new_path") or "",
                    body=body,
                    fingerprint=fingerprints[0] if fingerprints else None,
                    identity=identities[0] if identities else None,
                    # GitLab exposes no "the lines moved" signal, so resolution
                    # here rests entirely on the caller's validated allowlist.
                    outdated=False,
                )
            )
        self._active_findings = active
        return active

    def set_validated_fixed_threads(self, thread_ids: set[str]) -> None:
        """Install the allowlist of threads confirmed fixed and safe to resolve."""
        self._validated_fixed_thread_ids = set(thread_ids)

    def reply_in_thread(self, thread_id: str, body: str) -> None:
        """Reply to one existing discussion."""
        resp = self._client.post(
            f"{self._mr_api}/discussions/{thread_id}/notes",
            headers=self._headers,
            json={"body": body},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    def count_open_finding_threads(self) -> int:
        """How many of our finding discussions are still unresolved."""
        try:
            return len(
                [
                    d
                    for d in self._discussions()
                    if (notes := d.get("notes") or [])
                    and not notes[0].get("resolved")
                    and finding_keys(notes[0].get("body") or "")
                ]
            )
        except Exception as exc:  # noqa: BLE001 — disclosure metadata only
            _log.warning("counting open finding threads failed: %s", exc)
            return 0

    def apply_pr_labels(self, labels: list[str]) -> None:
        """Add lgtmaybe's labels to the merge request, best-effort."""
        if not labels:
            return
        try:
            resp = self._client.put(
                self._mr_api,
                headers=self._headers,
                json={"add_labels": ",".join(labels)},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — labels must never fail a review
            _log.warning("applying labels failed: %s", exc)

    def create_check_run(self, head_sha: str, conclusion: str, title: str, summary: str) -> None:
        """Publish the review outcome as a GitLab commit status, best-effort."""
        try:
            resp = self._client.post(
                f"{self._api}/statuses/{head_sha}",
                headers=self._headers,
                json={
                    "state": _STATUS_STATES.get(conclusion, "success"),
                    "name": "lgtmaybe",
                    "description": title[:255],
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — a status must never fail a review
            _log.warning("creating commit status failed: %s", exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _partition_findings(
        self,
        findings: list[ReviewFinding],
        commentable: CommentableLines,
    ) -> tuple[
        list[tuple[dict[str, Any], ReviewFinding]],
        list[ReviewFinding],
        list[ReviewFinding],
    ]:
        """Split findings into positioned discussions, body-demoted, and broad.

        Same rule as the other adapters — a finding goes inline only when it is
        confidently anchored AND lands on a real commentable diff line — with
        GitLab's position object substituted at the end. A finding is also
        demoted when the diff refs are unknown, since a position without them is
        rejected by the API.
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
            if self._diff_refs is None:
                demoted.append(f)
                continue
            position: dict[str, Any] = {
                **self._diff_refs,
                "position_type": "text",
                "old_path": f.path,
                "new_path": f.path,
            }
            # RIGHT is a line in the new file, LEFT a line in the old one.
            if f.side == "LEFT":
                position["old_line"] = f.line
            else:
                position["new_line"] = f.line
            inline.append((position, f))
        return inline, demoted, broad

    def _post_discussion(self, position: dict[str, Any], body: str) -> None:
        """Open one positioned discussion.

        Best-effort per finding: GitLab rejects a position whose line no longer
        exists in the current diff with a 400, and one unplaceable finding must
        not take the rest of the review down with it.
        """
        try:
            resp = self._client.post(
                f"{self._mr_api}/discussions",
                headers=self._headers,
                json={"body": body, "position": position},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — one rejected position is not fatal
            _log.warning("posting discussion at %s failed: %s", position.get("new_path"), exc)

    def _resolve_fixed_threads(self) -> None:
        """Reply in, then resolve, every thread the caller validated as fixed.

        Gated on the allowlist being installed: with no validation step there is
        no evidence a finding was fixed, and silently closing it would be worse
        than leaving it open.
        """
        if not self._validated_fixed_thread_ids:
            return
        for thread_id in sorted(self._validated_fixed_thread_ids):
            try:
                self.reply_in_thread(thread_id, _RESOLVED_REPLY)
                resp = self._client.put(
                    f"{self._mr_api}/discussions/{thread_id}",
                    headers=self._headers,
                    json={"resolved": True},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — resolving never fails a review
                _log.warning("resolving thread %s failed: %s", thread_id, exc)

    def _existing_finding_keys(self) -> set[str]:
        """Every hidden finding id already posted on this MR by us."""
        keys: set[str] = set()
        try:
            for discussion in self._discussions():
                for note in discussion.get("notes") or []:
                    keys |= finding_keys(note.get("body") or "")
        except Exception as exc:  # noqa: BLE001 — dedupe is best-effort
            _log.warning("reading existing discussions failed: %s", exc)
        return keys

    def _discussions(self) -> list[dict[str, Any]]:
        return self._paginate(f"{self._mr_api}/discussions")

    def _upsert_note(self, body: str, family: str) -> None:
        """Edit our previous note in this marker family, or post a new one."""
        existing_id = self._find_note(family)
        if existing_id is not None:
            resp = self._client.put(
                f"{self._mr_api}/notes/{existing_id}",
                headers=self._headers,
                json={"body": body},
                timeout=_TIMEOUT,
            )
        else:
            resp = self._client.post(
                f"{self._mr_api}/notes",
                headers=self._headers,
                json={"body": body},
                timeout=_TIMEOUT,
            )
        resp.raise_for_status()

    def _find_note(self, family: str) -> int | None:
        """The id of our existing note in ``family``, or None."""
        for note in self._paginate(f"{self._mr_api}/notes"):
            if family in (note.get("body") or ""):
                note_id = note.get("id")
                return int(note_id) if note_id is not None else None
        return None

    def _fetch_mr_diff(self) -> str:
        """The merge request's unified diff."""
        resp = self._client.get(
            f"{self._mr_api}/raw_diffs", headers=self._headers, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        return resp.text

    def _fetch_commit_subjects(self) -> list[str]:
        """Commit subject lines, feeding the intent lens. Never fails the review."""
        try:
            return [
                title
                for item in self._paginate(f"{self._mr_api}/commits")
                if (title := (item.get("title") or "").strip())
            ]
        except Exception as exc:  # noqa: BLE001 — intent is a nice-to-have
            _log.warning("fetching commit subjects failed: %s", exc)
            return []

    def _get_file_content(self, path: str, ref: str) -> str | None:
        """One file's text at ``ref``, or None when absent or undecodable."""
        try:
            resp = self._client.get(
                f"{self._api}/repository/files/{quote(path, safe='')}",
                headers=self._headers,
                params={"ref": ref},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            content = resp.json().get("content")
            if not isinstance(content, str):
                return None
            return base64.b64decode(content).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 — a missing file is not fatal
            _log.debug("fetching %s failed: %s", path, exc)
            return None

    def _get_json(self, url: str) -> Any:
        resp = self._client.get(url, headers=self._headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, url: str) -> list[dict[str, Any]]:
        """Every page of a GitLab list endpoint, flattened.

        Stops on the first short page rather than reading ``X-Next-Page``, so a
        response without pagination headers (and a mocked one) behaves the same.
        """
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = self._client.get(
                url,
                headers=self._headers,
                params={"page": page, "per_page": _PAGE_LIMIT},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                return items
            items.extend(batch)
            if len(batch) < _PAGE_LIMIT:
                return items
            page += 1


def _marker_values(body: str, family: str) -> list[str]:
    """Every hidden id of one marker family carried by a comment body."""
    import re

    return re.findall(rf"<!-- {re.escape(family)}:([0-9a-f]+) -->", body)
