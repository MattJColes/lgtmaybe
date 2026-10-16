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

4. **Incremental review rides the repository compare API.** It returns
   per-file diffs plus the commit list, so the increment is reassembled into a
   unified diff here, and "is this a plain push on top of the watermark?" is
   answered from the commits' parent ids (GitLab has no ``ahead`` status).
   GitLab also exposes no "the lines moved" flag on a discussion, so the
   adapter derives one from the diff since the note was positioned.
"""

from __future__ import annotations

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import httpx

from lgtmaybe.core.comment import (
    DIAGRAM_MARKER_FAMILY,
    DIAGRAMMED_MARKER,
    REVIEWED_MARKER,
    finding_keys,
    last_match,
    marker,
    partition_findings,
    render_broad,
    render_demoted,
    render_inline_body,
    stamped_ids,
    strip_markers,
    unposted,
)
from lgtmaybe.core.diff import (
    CommentableLines,
    build_commentable_lines,
    is_reviewable,
    is_scannable_manifest,
)
from lgtmaybe.core.diffbuild import has_unsafe_path_chars, patch_from_hunks
from lgtmaybe.core.diffparse import parse_hunk_header, walk_diff
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import ActiveFinding, PRContext, ReviewFinding
from lgtmaybe.core.paginate import paginate_pages

_log = get_logger(__name__)

_TIMEOUT = 30.0
_CONTENT_FETCH_WORKERS = 8

# GitLab's list endpoints page at 20 by default and cap at 100.
_PAGE_LIMIT = 100

# Written into a resolved thread before it is closed, so the trail explains
# itself to whoever reads the merge request later.
_RESOLVED_REPLY = "✅ Looks resolved."

# GitLab's smallest admin-configurable ``diff_max_files``. Past an instance's
# diff limits the compare API stops listing files and says nothing about it, so
# a list this long may already be cut short and cannot stand in for a review.
_COMPARE_MAX_FILES = 500

# Per-file flags GitLab sets when it withheld some or all of a file's patch.
_TRUNCATION_FLAGS = ("too_large", "collapsed", "overflow")

# Developer: the lowest GitLab role that can push, so the analogue of the GitHub
# write permission that feedback learning trusts.
_DEVELOPER_ACCESS = 30

# The award emoji name GitLab gives 👎.
_THUMBS_DOWN = "thumbsdown"


class GitLabGateway:
    """GitLab REST adapter.

    Args:
        host:      GitLab hostname, e.g. "gitlab.com" or a self-hosted instance.
        repo:      Full project path, e.g. "group/subgroup/project".
        pr_number: Merge request *iid* (the per-project number in the URL).
        token:     GitLab access token (project, group, or personal).
        client:    Injected httpx.Client; a default is created if omitted.
        resolve_fixed: Whether to resolve threads the caller validated as fixed.
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
        resolve_fixed: bool = True,
    ) -> None:
        self._repo = repo
        self._pr_number = pr_number
        # GitLab addresses a project by its URL-encoded full path, which is what
        # lets an arbitrarily nested group path travel in a URL segment.
        self._project = quote(repo, safe="")
        self._root = f"{scheme}://{host}/api/v4"
        self._api = f"{self._root}/projects/{self._project}"
        self._mr_api = f"{self._api}/merge_requests/{pr_number}"
        self._headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        # One setting across forges: without this GitLab resolved regardless of
        # what the user configured, while GitHub honoured it.
        self._resolve_fixed = resolve_fixed
        self._marker = marker("lgtmaybe", marker_key)
        self._describe_marker = marker("lgtmaybe-describe", marker_key)
        self._diagram_marker = marker(DIAGRAM_MARKER_FAMILY, marker_key)
        self._head_sha: str | None = None
        # The three SHAs every positioned discussion must carry. Cached from the
        # MR payload, because posting a finding needs them and post_review is
        # reachable without a preceding get_pr_context (a failure notice).
        self._diff_refs: dict[str, str] | None = None
        self._old_paths: dict[str, str] = {}
        self._scan_manifests = False
        self._active_findings: list[ActiveFinding] | None = None
        self._validated_fixed_thread_ids: set[str] | None = None
        # The MR's discussion list, read by three callers per run and never
        # mutated between them (see `_discussions`).
        self._discussions_cache: list[dict[str, Any]] | None = None
        self._notes_cache: list[dict[str, Any]] | None = None
        # Head SHA this run completed, set by the orchestrator on the success
        # path only. Never inferred inside post_review: a failure notice posts
        # through the same method and must not claim the head was reviewed.
        self._reviewed_sha: str | None = None
        # Files this incremental run re-reviewed; resolve-on-fix stays inside
        # them. None means a full review, so no restriction.
        self._incremental_paths: set[str] | None = None
        # The token's own user id. A watermark is only believed from a note this
        # identity wrote: anyone can post a note carrying our marker.
        self._posting_user_id: int | None = None
        self._posting_user_done = False
        # One compare per (from, to) pair: the increment and the outdated check
        # usually ask for the same one.
        self._compare_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        # Feedback learning asks once per reactor, not once per discussion.
        self._push_access_cache: dict[int, bool] = {}

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
                "base/head SHA, an unexpected GitLab API response."
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
        diff_items = list(self._paginate(f"{self._mr_api}/diffs"))
        changed_files = [item.get("new_path") or item.get("old_path") or "" for item in diff_items]
        changed_files = [path for path in changed_files if path]
        self._old_paths = {
            item["new_path"]: item["old_path"]
            for item in diff_items
            if item.get("new_path") and item.get("old_path")
        }

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

        The summary note carries the incremental-review watermark, under three
        rules:

        - Any ``<!-- lgtmaybe-reviewed:<sha> -->`` already in the summary or the
          demoted/broad findings is stripped. That text includes model prose,
          and a forged marker there must never become the watermark.
        - A new ``<!-- lgtmaybe-reviewed:<sha> -->`` is appended only when the
          orchestrator called ``mark_reviewed(sha)`` on the success path. A
          failure notice or partial run calls ``mark_reviewed(None)`` and can
          never stamp the new head.
        - An unmarked post carries forward the watermark already on the note it
          edits. The note is edited in place, so leaving it out would erase the
          record of commits that were reviewed; carrying it never moves it.
        """
        if diff is None and findings:
            diff = self._fetch_mr_diff()

        commentable: CommentableLines = build_commentable_lines(diff or "")
        inline, demoted, broad = self._partition_findings(findings, commentable, diff or "")

        if inline:
            inline = unposted(inline, self._existing_finding_keys())
        for position, finding in inline:
            if not self._post_discussion(position, render_inline_body(finding)):
                demoted.append(finding)

        # A watermark is only ever the one appended below, so any in the
        # content is stripped rather than left for a reader to mistake for
        # ours. Only that family: the summary carries the engine's own lenses
        # and risk markers, and finding prose is already stripped of every one.
        content = strip_markers(
            f"{summary}{render_demoted(demoted)}{render_broad(broad)}", REVIEWED_MARKER
        )
        body = f"{content}\n\n{self._marker}"
        # The note is edited in place, so an unmarked post (a failure notice, a
        # partial run) keeps the head the last successful run recorded instead
        # of erasing it: those commits were reviewed, and nothing since was.
        watermark = self._reviewed_sha or self._carried_marker(self._marker, REVIEWED_MARKER)
        if watermark:
            body += f"\n<!-- lgtmaybe-reviewed:{watermark} -->"
        self._upsert_note(body, self._marker)
        if self._resolve_fixed:
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

        ``completed_sha`` stamps the head whose required overview posted, the
        completion record incremental review reads when diagrams are required.
        A manual refresh (no SHA) keeps the previous stamp, and a marker inside
        the model-written diagram is stripped so content can never forge one.
        """
        body = strip_markers(body, DIAGRAMMED_MARKER).rstrip()
        stamp = completed_sha or self._carried_marker(self._diagram_marker, DIAGRAMMED_MARKER)
        body = f"{body}\n\n{self._diagram_marker}"
        if stamp:
            body += f"\n<!-- lgtmaybe-diagrammed:{stamp} -->"
        self._upsert_note(body, self._diagram_marker)

    def list_conversation_comments(self) -> list[tuple[str, str]]:
        """Every merge request note as ``(author, body)``, oldest first.

        GitLab's system notes ("added 1 commit") are events, not anyone's
        comment, so they are left out. Shares the notes fetch the upserts use.
        """
        return [
            ((note.get("author") or {}).get("username") or "", note.get("body") or "")
            for note in self._notes()
            if not note.get("system")
        ]

    def set_scan_manifests(self, enabled: bool) -> None:
        """Also fetch dependency-manifest text on the next context fetch."""
        self._scan_manifests = enabled

    # ------------------------------------------------------------------
    # Incremental review
    # ------------------------------------------------------------------

    def last_reviewed_sha(self) -> str | None:
        """The head recorded by our last successful review, or None.

        Read from the summary note's hidden watermark, and only from a note the
        token's own user wrote: a forged note claiming the current head is done
        would otherwise skip the review entirely. Any API failure, or an
        identity that cannot be established, returns None so the caller runs a
        full review.
        """
        try:
            note = self._own_note(self._marker, REVIEWED_MARKER)
        except (httpx.HTTPError, ValueError) as exc:
            _log.info("reading the review watermark failed — full review: %s", exc)
            return None
        return last_match(REVIEWED_MARKER, note.get("body")) if note is not None else None

    def last_completed_sha(self, *, diagram_required: bool) -> str | None:
        """The latest head whose required review outputs all posted.

        Without a required diagram that is the review watermark. With one, the
        diagram note's stamp is the record (it is written only after the review
        posted), provided our summary note exists at all, which is the rule the
        GitHub adapter applies too.
        """
        if not diagram_required:
            return self.last_reviewed_sha()
        try:
            if self._own_note(self._marker) is None:
                return None
            note = self._own_note(self._diagram_marker, DIAGRAMMED_MARKER)
        except (httpx.HTTPError, ValueError) as exc:
            _log.info("reading the diagram watermark failed — full review: %s", exc)
            return None
        return last_match(DIAGRAMMED_MARKER, note.get("body")) if note is not None else None

    def compare_diff(self, base_sha: str, head_sha: str) -> str | None:
        """The unified diff of the commits pushed since ``base_sha``, or None.

        Read-only, via the repository compare API (never a checkout). GitLab has
        no "ahead" status, so the commit list is checked instead: every commit
        must chain back through a single parent from ``head_sha`` to
        ``base_sha``. A force-push or rebase (the chain ends elsewhere), a merge,
        an unknown SHA (GitLab answers with an empty comparison), a compare that
        timed out or withheld any file's patch, an unsafe path, and any API
        failure all return None, so the caller falls back to a full review
        rather than trusting a meaningless increment.
        """
        comparison = self._compare(base_sha, head_sha)
        if comparison is None:
            return None
        reason = _unusable_increment(comparison, base_sha, head_sha)
        if reason is not None:
            _log.info("incremental compare not usable — falling back to full review: %s", reason)
            return None
        return _unified_diff(comparison["diffs"])

    def mark_reviewed(self, head_sha: str | None) -> None:
        """Prepare the watermark for the next summary post (success path only).

        In-memory until ``post_review`` writes it. The CLI's failure path calls
        ``mark_reviewed(None)`` so a failure notice never records the new head.
        """
        self._reviewed_sha = head_sha

    def set_incremental_scope(self, paths: set[str] | None) -> None:
        """Restrict resolve-on-fix to discussions on ``paths`` (None = no limit).

        A finding on a file this increment did not touch is missing from the
        run's findings only because nobody looked, so its thread stays open.
        """
        self._incremental_paths = paths

    def list_active_findings(self) -> list[ActiveFinding]:
        """Our own unresolved finding discussions, keyed by their hidden ids."""
        active: list[ActiveFinding] = []
        for discussion in self._discussions():
            notes = discussion.get("notes") or []
            if not notes:
                continue
            first = notes[0]
            if first.get("resolved") or not self._is_ours(first):
                continue  # resolved, or someone else's discussion
            body = first.get("body") or ""
            fingerprint, identity = stamped_ids(body)
            if fingerprint is None and identity is None:
                continue  # one of our replies elsewhere, not a finding
            position = first.get("position") or {}
            active.append(
                ActiveFinding(
                    thread_id=str(discussion.get("id")),
                    comment_id=first.get("id"),
                    path=position.get("new_path") or "",
                    body=body,
                    fingerprint=fingerprint,
                    identity=identity,
                    # GitLab exposes no "the lines moved" flag, so it is derived
                    # from the diff since the note was positioned.
                    outdated=self._is_outdated(position),
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
                    and self._is_ours(notes[0])
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
                    # GitLab's state vocabulary is narrower than GitHub's
                    # conclusions, but the one producer of `conclusion`
                    # (`cli._fail_on_check`) emits only "success"/"failure" —
                    # so a full mapping table is entries for inputs that never
                    # arrive. Only "failure" reds the commit; anything else
                    # keeps the fail-open default a best-effort status wants.
                    "state": "failed" if conclusion == "failure" else "success",
                    "name": "lgtmaybe",
                    "description": title[:255],
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — a status must never fail a review
            _log.warning("creating commit status failed: %s", exc)

    # ------------------------------------------------------------------
    # Feedback learning
    # ------------------------------------------------------------------

    def list_downvoted_fingerprints(self) -> set[str]:
        """Fingerprints of our findings a member who can push awarded 👎.

        Reads the ``thumbsdown`` award emoji on the first note of each of our
        finding discussions (resolved or not: a 👎 judges the finding, not the
        thread). On a public project anyone can award an emoji, so only a
        Developer-or-above member's counts, and an unverifiable reactor never
        suppresses anything. High and critical security findings are never
        suppressed this way, which the suppression pass enforces. A note whose
        awards cannot be read is skipped, so its finding is simply not
        suppressed.
        """
        candidates: list[tuple[int, str]] = []
        for discussion in self._discussions():
            notes = discussion.get("notes") or []
            if not notes:
                continue
            first = notes[0]
            fingerprint, _identity = stamped_ids(first.get("body"))
            note_id = first.get("id")
            if fingerprint is None or not isinstance(note_id, int) or not self._is_ours(first):
                continue  # not one of ours: anyone can paste our fingerprint
            candidates.append((note_id, fingerprint))
        if not candidates:
            return set()
        workers = min(_CONTENT_FETCH_WORKERS, len(candidates))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            reactors = list(pool.map(lambda c: self._thumbs_down_users(c[0]), candidates))
        return {
            fingerprint
            for (_note_id, fingerprint), users in zip(candidates, reactors, strict=True)
            if any(self._can_push(user) for user in sorted(users))
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _partition_findings(
        self,
        findings: list[ReviewFinding],
        commentable: CommentableLines,
        diff: str,
    ) -> tuple[
        list[tuple[dict[str, Any], ReviewFinding]],
        list[ReviewFinding],
        list[ReviewFinding],
    ]:
        """Split findings into positioned discussions, body-demoted, and broad.

        The placement rule is shared (``core.comment.partition_findings``); only
        the payload here is GitLab's position object, which carries both lines
        for a context line (the API rejects a context position naming one) and
        is demoted when the diff refs are unknown, since a position without
        them is rejected too.
        """
        context_lines: dict[tuple[str, int, str], tuple[int, int]] = {}
        for path, kind, old_line, new_line, _text in walk_diff(diff):
            if kind == " ":
                context_lines[path, old_line, "LEFT"] = old_line, new_line
                context_lines[path, new_line, "RIGHT"] = old_line, new_line

        def place(f: ReviewFinding) -> dict[str, Any] | None:
            if self._diff_refs is None:
                return None
            position: dict[str, Any] = {
                **self._diff_refs,
                "position_type": "text",
                "old_path": self._old_paths.get(f.path, f.path),
                "new_path": f.path,
            }
            if context := context_lines.get((f.path, f.line, f.side)):
                position["old_line"], position["new_line"] = context
            elif f.side == "LEFT":
                position["old_line"] = f.line
            else:
                position["new_line"] = f.line
            return position

        return partition_findings(findings, commentable, place)

    def _post_discussion(self, position: dict[str, Any], body: str) -> bool:
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
            return True
        except Exception as exc:  # noqa: BLE001 — one rejected position is not fatal
            _log.warning("posting discussion at %s failed: %s", position.get("new_path"), exc)
            return False

    def _resolve_fixed_threads(self) -> None:
        """Reply in, then resolve, every thread the caller validated as fixed.

        Gated on the allowlist being installed: with no validation step there is
        no evidence a finding was fixed, and silently closing it would be worse
        than leaving it open.
        """
        if not self._validated_fixed_thread_ids:
            return
        thread_ids = set(self._validated_fixed_thread_ids)
        if self._incremental_paths is not None:
            # An incremental run validated against its own increment only: a
            # thread on any other file has no fresh evidence behind it.
            paths = {f.thread_id: f.path for f in self._active_findings or []}
            thread_ids = {t for t in thread_ids if paths.get(t) in self._incremental_paths}
        for thread_id in sorted(thread_ids):
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

    def _is_ours(self, note: dict[str, Any]) -> bool:
        """Whether a note was written by the token's own user.

        Hidden ids are plain text anyone can type, so only our own notes count.
        An unknown identity fails closed: dedupe treats nothing as posted (a
        duplicate, never a hidden finding) and no thread is ours to resolve.
        """
        user_id = self._posting_user()
        return user_id is not None and (note.get("author") or {}).get("id") == user_id

    def _existing_finding_keys(self) -> list[set[str]]:
        """Hidden finding ids grouped by posted comment for one-for-one dedupe.

        Only notes the posting identity wrote count: the identity id is a
        prose-free hash an MR author can compute and paste ahead of the line.
        """
        keys: list[set[str]] = []
        try:
            for discussion in self._discussions():
                notes = discussion.get("notes") or []
                if notes and notes[0].get("resolved"):
                    continue
                for note in notes:
                    if self._is_ours(note) and (found := finding_keys(note.get("body") or "")):
                        keys.append(found)
        except Exception as exc:  # noqa: BLE001 — dedupe is best-effort
            _log.warning("reading existing discussions failed: %s", exc)
        return keys

    def _discussions(self) -> list[dict[str, Any]]:
        """Every discussion on the merge request, fetched once per run.

        Three callers read this list — `count_open_finding_threads` (via
        `get_pr_context`), `list_active_findings`, and `_existing_finding_keys`
        (via `post_review`) — and nothing between them writes a discussion:
        `post_review` only posts after `_existing_finding_keys` has read. So the
        second and third walks were re-fetching bytes this run already had.
        """
        if self._discussions_cache is None:
            self._discussions_cache = self._paginate(f"{self._mr_api}/discussions")
        return self._discussions_cache

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
        if existing_id is None and self._notes_cache is not None:
            # The cache predates this note, so a same-run re-read (a failure
            # notice after a posted summary) must not miss it and post twice.
            try:
                created = resp.json()
            except ValueError:
                created = None
            note = created if isinstance(created, dict) else {}
            self._notes_cache.append(
                {"id": note.get("id"), "body": body, "author": {"id": self._posting_user()}}
            )

    def _notes(self) -> list[dict[str, Any]]:
        """Every note on the merge request, fetched once per run."""
        if self._notes_cache is None:
            self._notes_cache = self._paginate(f"{self._mr_api}/notes")
        return self._notes_cache

    def _find_note(self, family: str) -> int | None:
        """The id of our own newest note in ``family``, or None.

        Our own, because anyone can post a note carrying the marker: a planted
        note as the upsert target would 403 on every run, and its stamp would
        be carried into ours. No own note, or an unknown identity, means a
        fresh note is posted.
        """
        note = self._own_note(family)
        if note is None:
            return None
        note_id = note.get("id")
        return int(note_id) if note_id is not None else None

    def _carried_marker(self, family: str, pattern: re.Pattern[str]) -> str | None:
        """The stamp already on our own note an upsert in ``family`` will edit.

        Carried forward so an edit that sets no new stamp keeps the old one.
        Only ever from a note our identity wrote, so a planted stamp can never
        be re-written under our name. A read failure carries nothing, which
        only costs the next run a full review.
        """
        try:
            note = self._own_note(family)
            return last_match(pattern, note.get("body")) if note is not None else None
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("reading the previous %s note failed: %s", family, exc)
            return None

    def _own_note(
        self, family: str, required: re.Pattern[str] | None = None
    ) -> dict[str, Any] | None:
        """Our own newest note in ``family`` (carrying ``required``, when given).

        Resolves the token's identity only when a candidate exists, so a merge
        request with no such note costs no extra request. An identity that
        cannot be established yields None: no note is trusted without it.
        "Newest" is decided by note id, not list position, so the answer does
        not depend on the order GitLab returns the notes in.
        """
        candidates = [
            note
            for note in self._notes()
            if family in (body := note.get("body") or "")
            and (required is None or required.search(body))
        ]
        if not candidates:
            return None
        user_id = self._posting_user()
        if user_id is None:
            return None
        return _newest(
            [note for note in candidates if (note.get("author") or {}).get("id") == user_id]
        )

    def _posting_user(self) -> int | None:
        """The token's own user id, fetched once per run; None when unknown.

        A failed lookup is "unknown", never an error: the upsert path reaches
        here too, and a flaky ``/user`` must not stop a summary (least of all a
        failure notice) from posting. Unknown trusts no note, so the cost is a
        fresh note this run and a full review next run.
        """
        if not self._posting_user_done:
            self._posting_user_done = True
            try:
                data = self._get_json(f"{self._root}/user")
            except (httpx.HTTPError, ValueError) as exc:
                _log.warning("looking up the token's own user failed: %s", exc)
                data = None
            user_id = data.get("id") if isinstance(data, dict) else None
            self._posting_user_id = user_id if isinstance(user_id, int) else None
        return self._posting_user_id

    def _compare(self, from_sha: str, to_sha: str) -> dict[str, Any] | None:
        """The straight (``git diff from to``) comparison, or None on any failure."""
        key = (from_sha, to_sha)
        if key not in self._compare_cache:
            comparison: dict[str, Any] | None = None
            try:
                resp = self._client.get(
                    f"{self._api}/repository/compare",
                    headers=self._headers,
                    params={"from": from_sha, "to": to_sha, "straight": "true"},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                payload = resp.json()
                comparison = payload if isinstance(payload, dict) else None
            except (httpx.HTTPError, ValueError) as exc:
                _log.info("compare %s..%s failed: %s", from_sha[:7], to_sha[:7], exc)
            self._compare_cache[key] = comparison
        return self._compare_cache[key]

    def _is_outdated(self, position: dict[str, Any]) -> bool:
        """Whether the code under a positioned note changed since it was placed.

        GitLab re-positions a note onto each new head while its line survives,
        so a note still positioned at an older head is one whose code moved. It
        is outdated when a hunk of the straight diff from that head to ours
        covers its new-side line (an old-side line the hunk actually spans, so a
        pure insertion next to the line does not count), or its file was
        deleted. The note's path is matched against both sides of a rename.
        Fails closed: an
        unknown head, a LEFT-side note (pushes do not change the base), a
        withheld patch, or any compare failure all read as not outdated, so
        nothing is resolved without evidence.
        """
        head = self._head_sha
        posted_at = position.get("head_sha")
        path = position.get("new_path")
        line = position.get("new_line")
        if not head or not isinstance(posted_at, str) or posted_at == head:
            return False
        if not path or not isinstance(line, int):
            return False
        diffs = (self._compare(posted_at, head) or {}).get("diffs")
        if not isinstance(diffs, list):
            return False
        for entry in diffs:
            if not isinstance(entry, dict) or path not in (
                entry.get("old_path"),
                entry.get("new_path"),
            ):
                continue
            if entry.get("deleted_file"):
                return True
            for raw in (entry.get("diff") or "").split("\n"):
                hunk = parse_hunk_header(raw)
                # A zero-length old side (``@@ -5,0 +6,2 @@``) is a pure
                # insertion: it spans no old line, so it outdates none.
                if hunk and hunk.old_start <= line < hunk.old_start + hunk.old_len:
                    return True
        return False

    def _thumbs_down_users(self, note_id: int) -> set[int]:
        """Ids of the users who awarded 👎 to one note; empty when unreadable."""
        try:
            awards = self._paginate(f"{self._mr_api}/notes/{note_id}/award_emoji")
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("reading award emoji on note %s failed: %s", note_id, exc)
            return set()
        users: set[int] = set()
        for award in awards:
            user_id = (award.get("user") or {}).get("id")
            if award.get("name") == _THUMBS_DOWN and isinstance(user_id, int):
                users.add(user_id)
        return users

    def _can_push(self, user_id: int) -> bool:
        """Whether a user is a Developer-or-above member; fails closed, cached."""
        cached = self._push_access_cache.get(user_id)
        if cached is not None:
            return cached
        allowed = False
        try:
            member = self._get_json(f"{self._api}/members/all/{user_id}")
            level = member.get("access_level") if isinstance(member, dict) else None
            allowed = isinstance(level, int) and level >= _DEVELOPER_ACCESS
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("membership check for user %s failed: %s", user_id, exc)
        self._push_access_cache[user_id] = allowed
        return allowed

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
        """Every page of a GitLab list endpoint, flattened."""
        return paginate_pages(
            self._client,
            url,
            self._headers,
            page_param="per_page",
            limit=_PAGE_LIMIT,
            timeout=_TIMEOUT,
        )


def _newest(notes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest note, by id (ids grow monotonically), or None.

    Independent of list order, which differs with the notes endpoint's ``sort``
    parameter. A note without an integer id ranks below any with one; ties keep
    the later list position.
    """
    if not notes:
        return None
    ranked = [
        (note_id if isinstance(note_id := note.get("id"), int) else -1, index)
        for index, note in enumerate(notes)
    ]
    return notes[max(ranked)[1]]


def _unusable_increment(comparison: dict[str, Any], base_sha: str, head_sha: str) -> str | None:
    """Why a comparison cannot stand in for a review of base..head, or None.

    The commit list is ``git log base..head``. On a plain push it is a chain of
    single-parent commits from head back to base; anything else (a rebase or
    force-push, where the chain ends somewhere other than base; a merge; a
    commit outside the chain; an empty list, GitLab's answer for an unknown SHA)
    means the diff does not describe "what was pushed since the review".
    """
    if comparison.get("compare_same_ref"):
        return "identical refs"
    if comparison.get("compare_timeout"):
        return "compare timed out"
    commits = comparison.get("commits")
    if not isinstance(commits, list) or not commits:
        return "no commits between the two SHAs"
    parents: dict[str, str] = {}
    for commit in commits:
        commit_id = commit.get("id") if isinstance(commit, dict) else None
        parent_ids = commit.get("parent_ids") if isinstance(commit, dict) else None
        if not isinstance(commit_id, str) or not isinstance(parent_ids, list):
            return "commit ancestry is not verifiable"
        if len(parent_ids) != 1 or not isinstance(parent_ids[0], str):
            return "the increment contains a merge"
        parents[commit_id] = parent_ids[0]
    current, walked = head_sha, 0
    while current != base_sha:
        if current not in parents or walked >= len(parents):
            return "head does not descend from the last reviewed commit"
        current, walked = parents[current], walked + 1
    if walked != len(parents):
        return "the commit list holds commits outside the push"
    diffs = comparison.get("diffs")
    if not isinstance(diffs, list):
        return "no file diffs"
    if len(diffs) >= _COMPARE_MAX_FILES:
        return "the file list may be cut short at GitLab's diff limit"
    for entry in diffs:
        if not isinstance(entry, dict):
            return "malformed file diff"
        if any(entry.get(flag) for flag in _TRUNCATION_FLAGS):
            return "GitLab withheld part of a file's patch"
        for key in ("old_path", "new_path"):
            path = entry.get(key)
            if not isinstance(path, str) or not path:
                return "a file diff names no path"
            if has_unsafe_path_chars(path):
                return "a file path cannot be written into a diff header safely"
    return None


def _unified_diff(entries: list[dict[str, Any]]) -> str:
    """Reassemble the compare API's per-file patches into one ``git diff``.

    Each entry already carries its hunks; what it lacks is the file header the
    shared diff parser keys on, which ``diffbuild.patch_from_hunks`` renders.
    Paths were vetted by ``_unusable_increment``.
    """
    parts: list[str] = []
    for entry in entries:
        parts.append(
            patch_from_hunks(
                old_path=None if entry.get("new_file") else entry["old_path"],
                new_path=None if entry.get("deleted_file") else entry["new_path"],
                old_mode=entry.get("a_mode"),
                new_mode=entry.get("b_mode"),
                hunks=entry.get("diff") or "",
            )
        )
    return "".join(parts)
