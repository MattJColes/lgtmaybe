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
3. **No resolvable threads.** Gitea has no ``resolveReviewThread`` equivalent,
   so this adapter does not claim ``SupportsThreadResolution`` — and, because
   an immutable review cannot be de-duplicated after the fact, it instead reads
   the hidden ids already on the PR and declines to post those findings again.

Also deliberately absent: incremental review (Gitea's compare API returns commit
metadata, not a unified diff, so there is nothing to review an increment from)
and base-branch checkout for symbol resolution. Both are optional capabilities,
so the CLI simply runs a full review without them.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Any

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
from lgtmaybe.core.models import PRContext, ReviewFinding

_log = get_logger(__name__)

# Matches the GitHub adapter: a cap on a hung socket, not on a slow-but-alive
# request. A self-hosted Gitea behind a slow proxy is the common case.
_TIMEOUT = 30.0

# Concurrency for the per-file head-content fetch. Independent GETs, so fetching
# them serially on a wide PR is pure round-trip latency.
_CONTENT_FETCH_WORKERS = 8

# Gitea pages most list endpoints at 50 by default; ask for the maximum.
_PAGE_LIMIT = 50

# Commit statuses are Gitea's equivalent of a check run. Its state vocabulary is
# narrower than GitHub's conclusions, so map rather than pass through.
_STATUS_STATES = {
    "success": "success",
    "neutral": "success",
    "skipped": "success",
    "failure": "failure",
    "action_required": "failure",
    "cancelled": "error",
    "timed_out": "error",
}


class GiteaGateway:
    """Gitea REST adapter.

    Args:
        host:      Gitea hostname, e.g. "gitea.example.com" (self-hosted is the norm).
        repo:      Full repo name, e.g. "owner/repo".
        pr_number: Pull-request index.
        token:     Gitea API token.
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
        self._describe_marker = marker("lgtmaybe-describe", marker_key)
        self._diagram_marker = marker("lgtmaybe-diagram", marker_key)
        self._head_sha: str | None = None
        self._scan_manifests = False

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
                f"base/head SHA — unexpected Gitea API response ({exc})."
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

    def post_review(
        self,
        findings: list[ReviewFinding],
        summary: str,
        diff: str | None = None,
    ) -> None:
        """Post inline comments as a review, and the summary as an upserted comment.

        Split in two because a Gitea review cannot be edited afterwards: the
        summary has to live somewhere re-runnable, and an issue comment is that
        place. Findings already carrying one of our hidden ids on the PR are
        dropped before posting, which is what keeps a re-run from duplicating
        every inline comment it made last time.
        """
        if diff is None and findings:
            diff = self._fetch_pr_diff()

        commentable: CommentableLines = build_commentable_lines(diff or "")
        inline, demoted, broad = self._partition_findings(findings, commentable)

        if inline:
            already = self._existing_finding_keys()
            inline = [
                (comment, f) for comment, f in inline if not (current_finding_keys([f]) & already)
            ]

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
                    "state": _STATUS_STATES.get(conclusion, "success"),
                    "context": "lgtmaybe",
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

        Same rule as the GitHub adapter — a finding goes inline only when it is
        confidently anchored AND lands on a real commentable diff line — with
        Gitea's position vocabulary substituted at the end.
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
            comment: dict[str, Any] = {"path": f.path, "body": render_inline_body(f)}
            # RIGHT is a line in the new file, LEFT a line in the old one.
            if f.side == "LEFT":
                comment["old_position"] = f.line
            else:
                comment["new_position"] = f.line
            inline.append((comment, f))
        return inline, demoted, broad

    def _existing_finding_keys(self) -> set[str]:
        """Every hidden finding id already posted on this PR by us.

        Best-effort: a failure here means a duplicate comment, which is far
        better than failing the whole review.
        """
        keys: set[str] = set()
        try:
            for review in self._paginate(f"{self._pr_api}/reviews"):
                review_id = review.get("id")
                if review_id is None:
                    continue
                for comment in self._paginate(f"{self._pr_api}/reviews/{review_id}/comments"):
                    keys |= finding_keys(comment.get("body") or "")
        except Exception as exc:  # noqa: BLE001 — dedupe is best-effort
            _log.warning("reading existing review comments failed: %s", exc)
        return keys

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
        """The id of our existing comment in ``family``, or None."""
        for comment in self._paginate(f"{self._issue_api}/comments"):
            if family in (comment.get("body") or ""):
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
                (item.get("commit", {}).get("message") or "").splitlines()[0]
                for item in self._paginate(f"{self._pr_api}/commits")
                if (item.get("commit", {}).get("message") or "").strip()
            ]
        except Exception as exc:  # noqa: BLE001 — intent is a nice-to-have
            _log.warning("fetching commit subjects failed: %s", exc)
            return []

    def _get_file_content(self, path: str, ref: str) -> str | None:
        """One file's text at ``ref``, or None when absent or undecodable."""
        try:
            resp = self._client.get(
                f"{self._api}/contents/{path}",
                headers=self._headers,
                params={"ref": ref},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            payload = resp.json()
            content = payload.get("content")
            if not isinstance(content, str):
                return None
            # Gitea returns base64 like GitHub; a binary file simply won't decode
            # as UTF-8, and is not reviewable anyway.
            return base64.b64decode(content).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 — a missing file is not fatal
            _log.debug("fetching %s failed: %s", path, exc)
            return None

    def _get_json(self, url: str) -> Any:
        resp = self._client.get(url, headers=self._headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, url: str) -> list[dict[str, Any]]:
        """Every page of a Gitea list endpoint, flattened.

        Gitea signals the end of a listing with a short page rather than a Link
        header, so this stops on the first page below the limit.
        """
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = self._client.get(
                url,
                headers=self._headers,
                params={"page": page, "limit": _PAGE_LIMIT},
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
