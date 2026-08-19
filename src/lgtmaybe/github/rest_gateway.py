"""RestGitHubGateway: talks to the GitHub REST API.

Implements GitHubGateway with:
- get_pr_context(): fetches diff + paginated file list + base/head SHAs.
- post_review(): batches inline comments + summary; idempotent via a marker comment.

The httpx.Client is injected so tests can use respx without monkey-patching.
All network calls carry an explicit timeout.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from lgtmaybe.core.comment import (
    FINDING_MARKER,
    IDENTITY_MARKER,
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
from lgtmaybe.core.models import (
    EFFORT_PREFIX,
    SECURITY_LABEL,
    SPLITTING_LABEL,
    ActiveFinding,
    PRContext,
    ReviewFinding,
)

from .checkout import clone_base_tree

_log = get_logger(__name__)

# GitHub's API is usually fast, but a cold runner behind a proxy — or a large
# PR's file listing — is not. The timeout exists to cap a hung socket, not to
# race a slow-but-healthy response into a failed review.
_TIMEOUT = httpx.Timeout(60.0)
_MARKER = "<!-- lgtmaybe -->"
_GRAPHQL_URL = "https://api.github.com/graphql"

# The PR's review-thread connection, paginated. Every caller wants the same
# connection and differs only in the ``nodes{…}`` selection it asks for, which
# is what the ``%s`` takes — see ``_walk_review_threads``.
_THREADS_QUERY = """
        query($owner:String!,$name:String!,$number:Int!,$cursor:String){
          repository(owner:$owner,name:$name){
            pullRequest(number:$number){
              reviewThreads(first:100, after:$cursor){
                pageInfo{ hasNextPage endCursor }
                nodes{ %s }
              }
            }
          }
        }
        """

# Stable name for the merge-gate Check Run (`fail_on`). Teams mark this exact
# name as a required status check in branch protection, so it must not change.
_CHECK_RUN_NAME = "lgtmaybe"

# When resolve-on-fix collapses a thread, the original comment's marker is
# rewritten into this disjoint "resolved" family so the active-marker scan
# (``_existing_finding_keys``) no longer sees it — a finding that
# reappears after being fixed posts again instead of being suppressed forever.
_ACTIVE_MARKER_PREFIX = "<!-- lgtmaybe-finding:"
_RESOLVED_MARKER_PREFIX = "<!-- lgtmaybe-resolved-fingerprint:"

# The identity family's own resolved-family prefix, mirroring the fingerprint's,
# so collapsing a thread hides both of a comment's markers from the active scan.
_ACTIVE_IDENTITY_PREFIX = "<!-- lgtmaybe-identity:"
_RESOLVED_IDENTITY_PREFIX = "<!-- lgtmaybe-resolved-identity:"

# Hidden marker stamped into the summary review body recording the head SHA
# this review covered, so the next run can review only the commits pushed
# since (commit-scoped incremental review). The capture group is the SHA.
_REVIEWED_MARKER = re.compile(r"<!-- lgtmaybe-reviewed:([0-9a-f]{7,40}) -->")
_DIAGRAMMED_MARKER = re.compile(r"<!-- lgtmaybe-diagrammed:([0-9a-f]{7,40}) -->")


# Concurrency for the per-file head-content fetch. The contents are independent
# GETs, so fetching them serially is pure round-trip latency on a many-file PR.
_CONTENT_FETCH_WORKERS = 8

# Concurrency for resolve-on-fix. Deliberately lower than the read pool above:
# these are writes (reply, resolve, marker rewrite) against one PR, and GitHub
# is stricter about concurrent mutations than concurrent reads.
_RESOLVE_WORKERS = 4


def _head_ref(meta: dict[str, Any]) -> str:
    """The PR's head branch name, or ``""`` when the payload doesn't carry one.

    Read by the spec lens to match a PR against the spec directory it is
    delivering. Defensive about shape because a fork PR whose head repository was
    deleted returns a partial ``head`` object.
    """
    ref = (meta.get("head") or {}).get("ref")
    return ref if isinstance(ref, str) else ""


def _first_comment(node: dict[str, Any]) -> dict[str, Any]:
    """A review thread's opening comment, or ``{}`` when it has none."""
    comments = node.get("comments", {}).get("nodes", [])
    return comments[0] if comments else {}


class RestGitHubGateway:
    """GitHub REST adapter.

    Args:
        repo:      Full repo name, e.g. "owner/repo".
        pr_number: Pull-request number.
        token:     GitHub personal access token or GITHUB_TOKEN.
        client:    Injected httpx.Client; a default is created if omitted.
    """

    def __init__(
        self,
        repo: str,
        pr_number: int,
        token: str,
        client: httpx.Client | None = None,
        marker_key: str | None = None,
        resolve_fixed: bool = True,
    ) -> None:
        self._repo = repo
        self._pr_number = pr_number
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # The JSON Accept header rides every REST call bar the raw-content and
        # ``.diff`` ones; the auth header stays out of the client's defaults so an
        # injected client (the tests') is never mutated.
        self._json_headers = {**self._headers, "Accept": "application/vnd.github+json"}
        self._api = f"https://api.github.com/repos/{repo}"
        self._pr_api = f"{self._api}/pulls/{pr_number}"
        self._issue_api = f"{self._api}/issues/{pr_number}"
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        # Three disjoint marker families: the review summary, the describe
        # comment, and the change diagram, so an update of one never clobbers
        # another. Each is scoped to the provider/model key when there is one.
        self._marker = marker("lgtmaybe", marker_key)
        self._describe_marker = marker("lgtmaybe-describe", marker_key)
        self._diagram_marker = marker("lgtmaybe-diagram", marker_key)
        self._resolve_fixed = resolve_fixed
        # Per-run cache of "does this login have write+ access?" — feedback
        # learning only trusts a 👎 from someone who can push, and a PR's
        # downvoters repeat across threads.
        self._perm_cache: dict[str, bool] = {}
        # Cached PR head SHA for read-only on-demand file fetches (get_file_contents),
        # populated lazily and reused so a deferral recheck doesn't re-fetch metadata.
        self._head_sha: str | None = None
        # Memoized PR metadata resource: three callers each want one field out of
        # the same GET, and nothing mutates the PR within a run.
        self._meta: Any | None = None
        # Lazily-cloned base tree for ast-grep symbol resolution (cloned at most once,
        # and only if a symbol deferral actually needs it). _done guards the one-shot.
        self._base_root: Path | None = None
        self._base_root_done = False
        # Incremental-review scope: when set (via set_incremental_scope), only
        # this run's reviewed paths. Resolve-on-fix then skips threads on other
        # paths — a finding absent merely because its file wasn't re-reviewed
        # this run must never be spuriously resolved. None = full review.
        self._incremental_paths: set[str] | None = None
        self._validated_fixed_thread_ids: set[str] | None = None
        self._active_findings: list[ActiveFinding] | None = None
        # Whether to fetch dependency manifests for the vulnerability scanner
        # (set via set_scan_manifests). Off by default so the overwhelming
        # majority of runs — static analysis is opt-in — pay no extra API calls.
        self._scan_manifests = False
        # Head SHA this run actually reviewed (set via mark_reviewed by the
        # orchestrator on the success path only). Drives the reviewed-SHA stamp
        # and re-run inline-comment posting. Deliberately NOT inferred inside
        # post_review: a failure notice posts through the same method and must
        # never record "reviewed up to head" — the next run would then skip
        # commits nobody reviewed.
        self._reviewed_sha: str | None = None
        # Memoized marker-review lookup: last_reviewed_sha (incremental) and
        # post_review both walk the full paginated reviews list for the same
        # marker, and nothing posts a review between the two reads within one
        # run — so the second walk is always identical. _done guards the
        # one-shot, since None is a valid "no marker review" result.
        self._existing_review_entry: tuple[int, str] | None = None
        self._existing_review_done = False

    # ------------------------------------------------------------------
    # GitHubGateway implementation
    # ------------------------------------------------------------------

    def get_pr_context(self) -> PRContext:
        """Fetch PR metadata, unified diff, and the full paginated files list."""
        # Fetch metadata (base/head SHAs)
        meta = self._pr_meta()
        try:
            base_sha: str = meta["base"]["sha"]
            head_sha: str = meta["head"]["sha"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                f"PR metadata for {self._repo}#{self._pr_number} is missing the "
                f"base/head SHA — unexpected GitHub API response ({exc})."
            ) from exc
        self._head_sha = head_sha  # cache for on-demand get_file_contents fetches

        # Fetch unified diff
        diff = self._fetch_pr_diff()

        # Fetch paginated files list
        changed_files = [
            item["filename"]
            for resp in self._paginate(f"{self._pr_api}/files?per_page=100")
            for item in resp.json()
        ]

        # Fetch head-revision text of reviewable files so the engine can pad hunks
        # with surrounding context. Read-only API fetch — never a checkout — and
        # the engine redacts it before it leaves the process. The fetches are
        # independent, so run them concurrently to cut round-trip latency.
        # The open-conversation count walks the reviewThreads connection, which is
        # a round trip (more on a PR with hundreds of threads) for what is only
        # disclosure metadata. It shares nothing with the content fetches, so it
        # rides the same pool rather than sitting in front of them: on the common
        # single-page PR the whole count hides inside the file-fetch latency.
        reviewable = [path for path in changed_files if is_reviewable(path)]
        # Dependency manifests for the vulnerability scanner. Lockfiles are not
        # reviewable, so they are fetched separately and kept out of
        # `file_contents` — see PRContext.scan_contents. Only fetched when
        # something will actually read them.
        scannable = (
            [path for path in changed_files if is_scannable_manifest(path)]
            if self._scan_manifests
            else []
        )
        file_contents: dict[str, str] = {}
        scan_contents: dict[str, str] = {}
        # +1, not a shared slot: the count must be free, and taking a worker
        # from the pool would narrow content fetching to
        # _CONTENT_FETCH_WORKERS - 1 for as long as the count runs — turning an
        # overlap into a slowdown on exactly the wide PRs the pool sizing is for.
        with ThreadPoolExecutor(max_workers=_CONTENT_FETCH_WORKERS + 1) as pool:
            open_threads = pool.submit(self.count_open_finding_threads)
            if scannable:
                scanned = pool.map(lambda p: (p, self._get_file_content(p, head_sha)), scannable)
                scan_contents = {path: text for path, text in scanned if text is not None}
            if reviewable:
                results = pool.map(lambda p: (p, self._get_file_content(p, head_sha)), reviewable)
                file_contents = {path: content for path, content in results if content is not None}
            open_finding_threads = open_threads.result()

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
            head_branch=_head_ref(meta),
            open_finding_threads=open_finding_threads,
        )

    def post_review(
        self,
        findings: list[ReviewFinding],
        summary: str,
        diff: str | None = None,
    ) -> None:
        """Post (or update) a single review with batched inline comments.

        If a previous review from this tool exists (identified by ``_MARKER`` in
        the body), it is updated in-place rather than creating a duplicate.

        The ``diff`` parameter is optional; when omitted the method fetches just
        the PR diff (one ``.diff``-Accept GET — never the full ``get_pr_context``
        fan-out) to build the commentable-line index. With no findings there is
        nothing to anchor, so even that fetch is skipped — a failure notice must
        not re-fetch anything (and must still post when a fetch is exactly what
        failed).
        """
        if diff is None and findings:
            diff = self._fetch_pr_diff()

        commentable: CommentableLines = build_commentable_lines(diff or "")
        inline, demoted, broad = self._partition_findings(findings, commentable)
        comments = [comment for comment, _finding in inline]

        body = f"{summary}{render_demoted(demoted)}{render_broad(broad)}\n\n{self._marker}"
        if self._reviewed_sha:
            # Record how far this review got, so the next run can review only
            # the commits pushed since (incremental review). Only stamped when
            # the orchestrator marked this run as a completed review — a
            # failure notice must not move the incremental watermark (and by
            # replacing the body it clears any stale stamp, so the next run
            # safely falls back to a full review).
            body += f"\n<!-- lgtmaybe-reviewed:{self._reviewed_sha} -->"
        existing = self._find_existing_review_entry()

        reviews_url = f"{self._pr_api}/reviews"

        if existing is not None:
            # Update the existing review body (inline comments cannot be changed
            # through this endpoint, but the summary is updated).
            update_url = f"{reviews_url}/{existing[0]}"
            resp = self._client.put(
                update_url,
                headers=self._json_headers,
                json={"body": body},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            # The review-update endpoint can't add inline comments, so post the
            # NEW findings (fingerprints not already on the PR) as individual
            # review comments — otherwise a re-run's fresh findings would only
            # ever appear in the summary body, silently losing their line.
            # Anchored to the completion watermark when there is one, else the
            # PR's current head: an incomplete run stamps no watermark, but the
            # findings its successful calls produced are real and still belong
            # inline — anchoring them to nothing silently dropped them (#443).
            rejected = self._post_new_inline_comments(
                inline, self._reviewed_sha or self._anchor_sha()
            )
            if rejected:
                body = (
                    f"{summary}{render_demoted([*demoted, *rejected])}"
                    f"{render_broad(broad)}\n\n{self._marker}"
                )
                if self._reviewed_sha:
                    body += f"\n<!-- lgtmaybe-reviewed:{self._reviewed_sha} -->"
                resp = self._client.put(
                    update_url,
                    headers=self._json_headers,
                    json={"body": body},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
            # A re-run: any of our prior conversations whose finding is now gone
            # (and whose code changed) is fixed — collapse it. Best-effort, so a
            # GraphQL hiccup never fails an otherwise-successful review.
            if self._resolve_fixed:
                self._resolve_fixed_threads(findings)
        else:
            payload: dict[str, Any] = {
                "body": body,
                "event": "COMMENT",
                "comments": comments,
            }
            resp = self._client.post(
                reviews_url,
                headers=self._json_headers,
                json=payload,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()

    def get_file_contents(self, path: str) -> str | None:
        """Return the head-revision text of *path*, or None if it can't be fetched.

        Read-only adapter method (beyond the frozen GitHubGateway port) used by the
        engine's reflection pass to resolve a deferred verdict: when the auditor
        needs to SEE a file it didn't get in the diff, this fetches that file's TEXT
        at the PR head via the same contents API ``get_pr_context`` uses — never a
        checkout, never executing PR code (fork-safe). The head SHA is fetched once
        and cached so repeated lookups in one review don't re-hit the PR metadata.
        """
        if self._head_sha is None:
            try:
                self._head_sha = self._pr_meta()["head"]["sha"]
            except httpx.HTTPError:
                return None
        return self._get_file_content(path, self._head_sha)

    def base_checkout_root(self) -> Path | None:
        """Lazily clone the PR's BASE tree (once) for ast-grep symbol resolution.

        On the GitHub path there is no working tree to search, so this shallow-clones
        the **base** branch — the trusted target repo, never the PR head/fork — into a
        throwaway temp dir. ast-grep only *parses* it (parsing is not executing), so it
        stays within the fork-safety model. Cloned at most once per gateway and only
        when a symbol deferral asks for a corpus; returns None if the base ref can't be
        resolved or the clone fails, in which case symbol resolution simply finds
        nothing. Suitable as the ``get_root`` callback of ``build_symbol_resolver``.
        """
        if not self._base_root_done:
            self._base_root_done = True
            ref = self._get_base_ref()
            self._base_root = clone_base_tree(self._repo, ref, self._token) if ref else None
        return self._base_root

    def _pr_meta(self) -> Any:
        """The PR metadata resource, fetched at most once per run.

        The base/head SHAs, the base ref, and the title/body all live in this one
        GET, and nothing mutates the PR mid-run — so the callers share it rather
        than each paying their own round trip. A failure is not cached: it raises
        (and the callers that must degrade to None still catch it), so a later
        caller retries rather than inheriting a transient error.
        """
        if self._meta is None:
            self._meta = self._get_json(self._pr_api)
        return self._meta

    def _get_base_ref(self) -> str | None:
        """The PR's base branch name (e.g. ``main``), or None if it can't be fetched."""
        try:
            ref = self._pr_meta()["base"]["ref"]
        except (httpx.HTTPError, KeyError, TypeError):
            return None
        return ref if isinstance(ref, str) and ref else None

    def post_issue_comment(self, body: str) -> None:
        """Post a standalone comment to the PR conversation (in-thread reply).

        Used by slash commands (/ask, /describe). Beyond the frozen GitHubGateway
        port, which only models reviews.
        """
        url = f"{self._issue_api}/comments"
        resp = self._client.post(
            url,
            headers=self._json_headers,
            json={"body": body},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    def post_describe_comment(self, body: str) -> None:
        """Post or update the structured PR-description comment, idempotently.

        Finds our previous description by its hidden marker and edits it in
        place, so a re-run (or auto-describe after new pushes) never stacks
        duplicate description comments. Adapter-only, beyond the frozen port.
        """
        self._upsert_marked_comment(body, self._describe_marker)

    def post_diagram_comment(self, body: str, *, completed_sha: str | None = None) -> None:
        """Post or update the change-diagram comment, idempotently.

        Its marker family is disjoint from the describe and review markers, so
        the three comments never clobber each other. Adapter-only, beyond the
        frozen port.
        """
        body = _DIAGRAMMED_MARKER.sub("", body).rstrip()
        if completed_sha is not None:
            body = f"{body}\n\n<!-- lgtmaybe-diagrammed:{completed_sha} -->"
        self._upsert_marked_comment(body, self._diagram_marker, preserve=_DIAGRAMMED_MARKER)

    def _upsert_marked_comment(
        self, body: str, marker: str, *, preserve: re.Pattern[str] | None = None
    ) -> None:
        """Post *body* as an issue comment stamped with *marker*, or edit the
        existing comment carrying that marker in place."""
        stamped = f"{body}\n\n{marker}"
        url = f"{self._issue_api}/comments"
        for resp in self._paginate(f"{url}?per_page=100"):
            for comment in resp.json():
                if marker in (comment.get("body") or ""):
                    if preserve is not None and preserve.search(stamped) is None:
                        previous = preserve.search(comment.get("body") or "")
                        if previous is not None:
                            stamped += f"\n{previous.group(0)}"
                    patched = self._client.patch(
                        f"{self._api}/issues/comments/{comment['id']}",
                        headers=self._json_headers,
                        json={"body": stamped},
                        timeout=_TIMEOUT,
                    )
                    patched.raise_for_status()
                    return
        created = self._client.post(
            url,
            headers=self._json_headers,
            json={"body": stamped},
            timeout=_TIMEOUT,
        )
        created.raise_for_status()

    def apply_pr_labels(self, labels: list[str]) -> None:
        """Reconcile our managed PR labels to exactly *labels*. Best-effort.

        Only labels this tool owns (the ``review-effort/`` family,
        ``possible-security-issue``, ``consider-splitting``) are ever removed —
        anything a human applied is untouched. Any API failure is logged and
        swallowed: a labelling hiccup must never fail an otherwise-successful
        review. Adapter-only, beyond the frozen port.
        """
        try:
            base = self._issue_api
            current: set[str] = {
                item["name"]
                for resp in self._paginate(f"{base}/labels?per_page=100")
                for item in resp.json()
            }
            managed = {
                name
                for name in current
                if name.startswith(EFFORT_PREFIX) or name in (SECURITY_LABEL, SPLITTING_LABEL)
            }
            for stale in sorted(managed - set(labels)):
                # The label name is a single path segment — quote it fully, or
                # the slash in "review-effort/2" would read as a path separator.
                resp = self._client.delete(
                    f"{base}/labels/{quote(stale, safe='')}",
                    headers=self._json_headers,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
            to_add = sorted(set(labels) - current)
            if to_add:
                resp = self._client.post(
                    f"{base}/labels",
                    headers=self._json_headers,
                    json={"labels": to_add},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — labels are auxiliary, never fatal
            _log.warning("applying PR labels failed: %s", exc)

    def create_check_run(self, head_sha: str, conclusion: str, title: str, summary: str) -> None:
        """Create a completed Check Run on *head_sha* — the merge-gate (`fail_on`).

        POSTs a `completed` check run whose *conclusion* (`failure`/`success`)
        a team can require in branch protection, so a blocking finding stops the
        merge. Enforcement rides the Check Run, never PR approval state (lgtmaybe
        never sets approval state). Adapter-only, beyond the frozen port.
        """
        url = f"{self._api}/check-runs"
        resp = self._client.post(
            url,
            headers=self._json_headers,
            json={
                "name": _CHECK_RUN_NAME,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": title, "summary": summary},
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Incremental review (adapter-only methods, beyond the frozen port)
    # ------------------------------------------------------------------

    def last_reviewed_sha(self) -> str | None:
        """The head SHA covered by our previous review, or None.

        Read back from the hidden ``<!-- lgtmaybe-reviewed:… -->`` marker that
        ``post_review`` stamps into the summary body. None when there is no
        prior review, the review predates the marker, or the lookup fails —
        the caller then runs a full review.
        """
        try:
            entry = self._find_existing_review_entry()
        except httpx.HTTPError:
            return None
        if entry is None:
            return None
        _review_id, body = entry
        match = _REVIEWED_MARKER.search(body)
        return match.group(1) if match else None

    def last_completed_sha(self, *, diagram_required: bool) -> str | None:
        """Return the latest head whose required review outputs were posted.

        A diagram marker is written only after its review post succeeds, so it
        is the durable completion record when diagrams are required. It may be
        older than the summary's reviewed marker after an interrupted newer
        attempt; returning it preserves the last genuinely completed head.
        """
        if not diagram_required:
            return self.last_reviewed_sha()
        try:
            if self._find_existing_review_entry() is None:
                return None
            url = f"{self._issue_api}/comments?per_page=100"
            for resp in self._paginate(url):
                for comment in resp.json():
                    body = comment.get("body") or ""
                    if self._diagram_marker not in body:
                        continue
                    match = _DIAGRAMMED_MARKER.search(body)
                    return match.group(1) if match else None
        except httpx.HTTPError:
            return None
        return None

    def compare_diff(self, base_sha: str, head_sha: str) -> str | None:
        """Unified diff of the commits between *base_sha* and *head_sha*, or None.

        Uses the compare API (read-only — never a checkout). Returns the diff
        only when head is strictly **ahead** of the last-reviewed SHA (a normal
        push). A force-push/rebase (``diverged``/``behind``), an ``identical``
        compare, and any API failure (e.g. a GC'd SHA 404ing after a
        force-push) all return None — the caller falls back to a full review
        rather than trusting a meaningless increment.
        """
        url = f"{self._api}/compare/{base_sha}...{head_sha}"
        try:
            status = self._get_json(url).get("status")
            if status != "ahead":
                _log.info(
                    "incremental compare not usable — falling back to full review",
                    extra={"status": status},
                )
                return None
            resp = self._client.get(
                url,
                headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            _log.info("incremental compare failed — falling back to full review: %s", exc)
            return None
        return resp.text

    def set_scan_manifests(self, enabled: bool) -> None:
        """Fetch changed dependency manifests for scanning on the next context read.

        Off by default: `get_pr_context` takes no config, and fetching lockfiles
        for every review would cost API calls nothing would read. The CLI turns
        it on when static analysis is enabled.
        """
        self._scan_manifests = enabled

    def set_incremental_scope(self, paths: set[str] | None) -> None:
        """Restrict resolve-on-fix to threads on *paths* (None = no restriction).

        Set by the incremental-review path with the files actually re-reviewed
        this run: a finding on any other file is absent from this run's
        findings only because its hunks weren't in the increment, so its
        conversation must stay open rather than be spuriously resolved.
        """
        self._incremental_paths = paths

    def list_active_findings(self) -> list[ActiveFinding]:
        """Read our unresolved finding roots for explicit follow-up validation."""
        active: list[ActiveFinding] = []
        for node in self._walk_review_threads(
            "id isResolved isOutdated path comments(first:1){ nodes{ body databaseId } }"
        ):
            if node.get("isResolved"):
                continue
            first = _first_comment(node)
            body = first.get("body", "") or ""
            fingerprint = FINDING_MARKER.search(body)
            identity = IDENTITY_MARKER.search(body)
            if fingerprint is None and identity is None:
                continue
            active.append(
                ActiveFinding(
                    thread_id=node["id"],
                    comment_id=first.get("databaseId"),
                    path=node.get("path") or "",
                    body=body,
                    fingerprint=fingerprint.group(1) if fingerprint else None,
                    identity=identity.group(1) if identity else None,
                    outdated=bool(node.get("isOutdated")),
                )
            )
        self._active_findings = active
        return active

    def set_validated_fixed_threads(self, thread_ids: set[str]) -> None:
        """Restrict hybrid resolve-on-fix to explicitly validated thread ids."""
        self._validated_fixed_thread_ids = set(thread_ids)

    def mark_reviewed(self, head_sha: str | None) -> None:
        """Prepare the reviewed marker for the next successful review post.

        This setter changes only in-memory request state; GitHub receives the
        marker atomically inside ``post_review``. The CLI's failure path calls
        ``mark_reviewed(None)`` so a later failure notice never stamps
        ``<!-- lgtmaybe-reviewed:... -->`` — a failed run must not move the
        watermark.
        """
        self._reviewed_sha = head_sha

    def _anchor_sha(self) -> str | None:
        """The PR's current head SHA, for anchoring re-run inline comments.

        The fallback when the completion watermark is absent — i.e. an
        incomplete run, whose ``mark_reviewed(None)`` correctly suppresses the
        watermark stamp but must not also suppress the findings the run did
        compute. The head cached by ``get_pr_context``, with the same lazy
        metadata fetch ``get_file_contents`` uses when no context was fetched;
        None when even that fails, in which case the caller demotes rather than
        drops.
        """
        if self._head_sha is None:
            try:
                self._head_sha = self._pr_meta()["head"]["sha"]
            except (httpx.HTTPError, KeyError, TypeError):
                return None
        return self._head_sha

    def _post_new_inline_comments(
        self,
        inline: list[tuple[dict[str, Any], ReviewFinding]],
        head_sha: str | None,
    ) -> list[ReviewFinding]:
        """Post the inline comments whose finding isn't already on the PR.

        The review-update endpoint only replaces the body, so on a re-run new
        findings are posted as individual review comments (anchored to
        *head_sha*). Each comment body already carries its hidden ids
        (fingerprint + identity), and a candidate is matched to an already-posted
        comment when they share **either** — which is what makes dedupe survive
        the model rephrasing the finding, since that changes the fingerprint but
        not the identity.

        Matching is **one-for-one**: each existing comment can absorb at most one
        candidate, and only unmatched candidates post. Set membership alone would
        be wrong, because an identity is not unique within a file — two findings
        from the same lens on two *identical* source lines (``return None``
        twice) share one identity. Counting occurrences keeps both behaviours:
        a re-run of the same N findings posts nothing, while an N+1th occurrence
        the last run missed is still a new finding and still posts.

        A comment GitHub rejects with 422 is returned to the caller for demotion
        into the review body; later comments still post. Other errors remain
        fatal. Without a head SHA there is nothing to anchor to, so unmatched
        candidates are demoted into the review body the same way — never
        dropped silently.
        """
        if not inline:
            return []
        unmatched = self._existing_finding_keys()
        url = f"{self._pr_api}/comments"
        rejected: list[ReviewFinding] = []
        for comment, finding in inline:
            keys = finding_keys(comment.get("body", ""))
            already = next((i for i, e in enumerate(unmatched) if e & keys), None)
            if already is not None:
                unmatched.pop(already)  # consumed — it can't absorb a second candidate
                continue
            if head_sha is None:
                _log.warning(
                    "no head SHA to anchor a new inline comment at %s:%s:%s — "
                    "demoting to review body",
                    finding.path,
                    finding.line,
                    finding.side,
                )
                rejected.append(finding)
                continue
            resp = self._client.post(
                url,
                headers=self._json_headers,
                json={**comment, "commit_id": head_sha},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 422:
                try:
                    error = resp.json()
                except ValueError:
                    error = {}
                message = error.get("message") if isinstance(error, dict) else None
                raw_errors = error.get("errors") if isinstance(error, dict) else None
                errors = (
                    [
                        {key: item[key] for key in ("resource", "field", "code") if key in item}
                        for item in raw_errors
                        if isinstance(item, dict)
                    ]
                    if isinstance(raw_errors, list)
                    else None
                )
                _log.warning(
                    "GitHub rejected inline comment at %s:%s:%s with 422: "
                    "message=%r errors=%r; demoting to review body",
                    finding.path,
                    finding.line,
                    finding.side,
                    message,
                    errors,
                )
                rejected.append(finding)
                continue
            resp.raise_for_status()
        return rejected

    def _existing_finding_keys(self) -> list[set[str]]:
        """Hidden ids of the lgtmaybe findings already posted inline on the PR.

        One entry **per posted comment** (its fingerprint and identity together),
        not one pooled set, so the caller can match candidates to occurrences
        one-for-one — an identity repeats when a file has two identical flagged
        lines. Only the active marker families are collected, so a thread
        collapsed by resolve-on-fix (its markers rewritten into the resolved
        families) stops suppressing — a finding that comes back posts again.
        """
        url = f"{self._pr_api}/comments?per_page=100"
        posted: list[set[str]] = []
        for resp in self._paginate(url):
            for item in resp.json():
                keys = finding_keys(item.get("body", "") or "")
                if keys:
                    posted.append(keys)
        return posted

    # ------------------------------------------------------------------
    # Resolve-on-fix thread replies (adapter-only, beyond the frozen port)
    # ------------------------------------------------------------------

    def reply_in_thread(self, thread_id: str, body: str) -> None:
        """Post *body* as a reply on review thread *thread_id* (a GraphQL node id).

        Resolve-on-fix uses ``addPullRequestReviewThreadReply`` to explain why a
        verified outdated finding is being closed. Adapter-only, beyond the
        frozen port.
        """
        mutation = """
        mutation($threadId:ID!,$body:String!){
          addPullRequestReviewThreadReply(
            input:{pullRequestReviewThreadId:$threadId, body:$body}
          ){ comment{ id } }
        }
        """
        self._graphql(mutation, {"threadId": thread_id, "body": body})

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _walk_review_threads(self, node_fields: str) -> Iterator[dict[str, Any]]:
        """Yield every review thread on the PR, following GraphQL pagination.

        *node_fields* is the ``nodes{…}`` selection the caller needs; everything
        else — the query skeleton, the four variables, the connection unwrap and
        the cursor advance — is the same for every caller. Read-only.
        """
        owner, _, name = self._repo.partition("/")
        cursor: str | None = None
        while True:
            data = self._graphql(
                _THREADS_QUERY % node_fields,
                {"owner": owner, "name": name, "number": self._pr_number, "cursor": cursor},
            )
            conn = data["repository"]["pullRequest"]["reviewThreads"]
            yield from conn["nodes"]
            page = conn.get("pageInfo", {})
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")

    def _get_json(self, url: str) -> Any:
        resp = self._client.get(
            url,
            headers=self._json_headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _fetch_pr_diff(self) -> str:
        """The PR's unified diff — a single GET with the ``.diff`` Accept header."""
        resp = self._client.get(
            self._pr_api,
            headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text

    def _get_file_content(self, path: str, ref: str) -> str | None:
        """Return the raw text of *path* at *ref*, or None if it can't be fetched.

        Deleted/renamed-away files (404) and any other fetch error degrade to
        None so the engine simply reviews the bare diff for that file.
        """
        url = f"{self._api}/contents/{path}?ref={ref}"
        try:
            resp = self._client.get(
                url,
                headers={**self._headers, "Accept": "application/vnd.github.v3.raw"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        return resp.text

    def _fetch_commit_subjects(self) -> list[str]:
        """First line of each commit message on the PR (the commit "name").

        Stated-intent context for the engine's intent lens. Auxiliary, so it
        degrades like file contents: any fetch error returns [] (the intent lens
        still has the PR title/description) rather than failing the review.
        """
        url = f"{self._pr_api}/commits?per_page=100"
        subjects: list[str] = []
        try:
            for resp in self._paginate(url):
                for item in resp.json():
                    message: str = (item.get("commit") or {}).get("message") or ""
                    first_line = message.splitlines()[0].strip() if message else ""
                    if first_line:
                        subjects.append(first_line)
        except httpx.HTTPError:
            return []
        return subjects

    def _paginate(self, url: str) -> Iterator[httpx.Response]:
        next_url: str | None = url
        while next_url is not None:
            resp = self._client.get(
                next_url,
                headers=self._json_headers,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            yield resp
            # httpx parses the Link header for us.
            next_url = resp.links.get("next", {}).get("url")

    def _find_existing_review_entry(self) -> tuple[int, str] | None:
        """Return ``(id, body)`` of the first review carrying our marker, or None.

        Follows Link rel=next pagination — a busy PR can hold more than one page
        of reviews, and missing the marker there would duplicate the review
        instead of updating it. Memoized for the run: the incremental watermark
        read and the post both need it, and no review is posted in between.
        """
        if self._existing_review_done:
            return self._existing_review_entry
        for resp in self._paginate(f"{self._pr_api}/reviews"):
            for review in resp.json():
                body: str = review.get("body", "") or ""
                if self._marker in body:
                    review_id: int = review["id"]
                    self._existing_review_entry = (review_id, body)
                    self._existing_review_done = True
                    return self._existing_review_entry
        self._existing_review_done = True
        return None

    # ------------------------------------------------------------------
    # Auto-resolve fixed conversations (GraphQL — the REST review API can't
    # resolve a review thread).
    # ------------------------------------------------------------------

    def _resolve_fixed_threads(self, findings: list[ReviewFinding]) -> None:
        """Resolve our prior conversations whose finding is gone and code changed.

        A thread is "fixed" when neither of its hidden ids (fingerprint or
        identity) is produced by this run AND GitHub marks it outdated (the lines
        it anchored to changed). Matching on either id matters as much here as it
        does for dedupe: keyed on the prose-derived fingerprint alone, a reworded
        finding reads as gone, so the thread would be resolved and its markers
        retired — and the "same" finding would post fresh on the next run.
        Each fixed thread gets a short reply for the audit trail, then collapses,
        and its opening comment's fingerprint marker is rewritten into the
        "resolved" family so a reintroduced finding is not suppressed forever.

        Entirely best-effort: any failure is logged and swallowed so an
        auto-resolve hiccup can never fail an otherwise-successful review.
        Best-effort **per thread** — the threads are independent, so one that
        fails is logged and the rest still resolve rather than being abandoned
        wherever the loop happened to stop.
        """
        try:
            current = current_finding_keys(findings)
            fixed = self._fixed_threads(current)
        except Exception as exc:  # noqa: BLE001 — best-effort; never fail the review
            _log.warning("auto-resolve of fixed conversations failed: %s", exc)
            return
        if not fixed:
            return

        # A refusal is a property of the identity, not of the thread: it will
        # recur on every remaining thread and every future run until the setup
        # changes. The first one trips this and the rest are skipped, so a
        # misconfigured identity costs at most one WAVE of forbidden calls
        # (_RESOLVE_WORKERS) and exactly one warning, instead of one of each per
        # thread. Not exactly one call: workers already in flight when the flag
        # is set have passed the check. Bounding it to the pool width is the
        # point — serialising a probe first would slow the healthy path to save
        # three futile calls on the broken one.
        refused = threading.Event()

        def resolve_one(thread: tuple[str, int | None, str]) -> None:
            """Retire one finding marker, resolve its thread, then reply.

            Three steps with three different consequences, so they are sequenced
            so the unsafe failure state — a resolved thread with active markers —
            is impossible:

            1. **Rewrite the marker** — correctness-critical. If this fails the
               thread stays open and retryable.
            2. **Resolve** — if this fails, restore the active body. A failed
               restore may cause a duplicate later, but can never suppress a
               reintroduced finding.
            3. **Reply** — cosmetic audit trail. Last, because a failure here
               must not affect either durable state transition.
            """
            if refused.is_set():
                return
            thread_id, comment_id, first_body = thread
            try:
                self._mark_comment_resolved(comment_id, first_body)
            except Exception as exc:  # noqa: BLE001 — one thread never blocks the rest
                _log.warning("resolved-marker rewrite on %s failed: %s", thread_id, exc)
                return
            try:
                self._resolve_thread(thread_id)
            except Exception as exc:  # noqa: BLE001 — one thread never blocks the rest
                try:
                    self._restore_comment_active(comment_id, first_body)
                except Exception as restore_exc:  # noqa: BLE001 — safe duplicate failure mode
                    _log.warning(
                        "restoring active markers on thread %s failed: %s",
                        thread_id,
                        restore_exc,
                    )
                if "FORBIDDEN" in str(exc) or "not accessible by integration" in str(exc):
                    if not refused.is_set():
                        refused.set()
                        _log.warning(
                            "auto-resolve is not permitted for this identity — threads stay "
                            "open. Grant the token/App pull-request write access (the public "
                            "lgtmaybe App cannot resolve threads), or set resolve_fixed: false "
                            "to stop attempting it. (%s)",
                            exc,
                        )
                else:
                    _log.warning("auto-resolve of thread %s failed: %s", thread_id, exc)
                return
            try:
                self.reply_in_thread(thread_id, "✅ Looks resolved.")
            except Exception as exc:  # noqa: BLE001 — nothing depends on the reply
                _log.warning("resolved-thread reply on %s failed: %s", thread_id, exc)

        # Each thread costs a reply + a resolve + a marker rewrite; a PR with
        # several fixed findings paid all of it serially. They touch different
        # threads, so they overlap on the shared (thread-safe) httpx client.
        with ThreadPoolExecutor(max_workers=min(_RESOLVE_WORKERS, len(fixed))) as pool:
            list(pool.map(resolve_one, fixed))

    def count_open_finding_threads(self) -> int:
        """How many of OUR finding conversations are still unresolved on this PR.

        The business a run's own finding count cannot see. An incremental run
        reviews only the newest commits, so an earlier finding on an untouched
        file never reappears — its absence is not evidence it was fixed. The
        engine uses this to keep "👍 LGTM!" off a PR that still has open
        conversations (see `PRContext.open_finding_threads`).

        Counts only threads whose opening comment carries our ACTIVE finding
        marker: resolve-on-fix rewrites that marker into the "resolved" family,
        so a thread we already closed is excluded by construction, as is any
        thread we did not open. Best-effort and read-only — any failure returns
        0 rather than blocking a review over a disclosure nicety. Adapter-only,
        beyond the frozen port.
        """
        open_threads = 0
        try:
            for node in self._walk_review_threads("isResolved comments(first:1){ nodes{ body } }"):
                if node.get("isResolved"):
                    continue
                if FINDING_MARKER.search(_first_comment(node).get("body", "")):
                    open_threads += 1
        except Exception as exc:  # noqa: BLE001 — disclosure is never worth a failed review
            _log.warning("counting open finding conversations failed: %s", exc)
            return 0
        return open_threads

    def _fixed_threads(self, current: set[str]) -> list[tuple[str, int | None, str]]:
        """Our unresolved, outdated conversations whose finding is gone.

        Each entry is ``(thread_id, opening comment's REST id or None, opening
        comment's body)`` — the comment id/body feed the resolved-marker rewrite.
        """
        if self._validated_fixed_thread_ids is not None and self._active_findings is not None:
            return [
                (finding.thread_id, finding.comment_id, finding.body)
                for finding in self._active_findings
                if finding.thread_id in self._validated_fixed_thread_ids
            ]

        fixed: list[tuple[str, int | None, str]] = []
        for node in self._walk_review_threads(
            "id isResolved isOutdated path comments(first:1){ nodes{ body databaseId } }"
        ):
            if node.get("isResolved"):
                continue
            if self._validated_fixed_thread_ids is not None:
                if node.get("id") not in self._validated_fixed_thread_ids:
                    continue
            else:
                if not node.get("isOutdated"):
                    continue
                if (
                    self._incremental_paths is not None
                    and node.get("path") not in self._incremental_paths
                ):
                    continue
            first = _first_comment(node)
            first_body = first.get("body", "")
            keys = finding_keys(first_body)
            if not keys:
                continue  # not one of ours
            if self._validated_fixed_thread_ids is None and keys & current:
                continue  # still flagged this run (however worded) — leave it open
            fixed.append((node["id"], first.get("databaseId"), first_body))
        return fixed

    def _resolve_thread(self, thread_id: str) -> None:
        """Mark a review thread resolved. Raises if the identity may not.

        The gate for the whole resolve-on-fix step (see `resolve_one`): nothing
        else may happen until this succeeds. Replying first meant a resolve the
        app isn't permitted to make (GitHub answers FORBIDDEN, and GraphQL errors
        arrive as HTTP 200) left "✅ Looks resolved." on a thread that stayed
        open — which then re-qualified as fixed on every later run and collected
        another reply each time.
        """
        resolve = """
        mutation($threadId:ID!){
          resolveReviewThread(input:{threadId:$threadId}){ thread{ id isResolved } }
        }
        """
        self._graphql(resolve, {"threadId": thread_id})

    def _mark_comment_resolved(self, comment_id: int | None, body: str) -> None:
        """Rewrite a resolved comment's fingerprint marker into the "resolved" family.

        ``_existing_finding_keys`` matches only the active marker families, so
        without this rewrite a finding fixed once would be skipped forever if it
        reappeared. **Both** families are retired together — leaving the identity
        marker active would keep suppressing the finding after its fingerprint
        was retired. A failure raises so the caller can reopen the thread and
        preserve retryable state.
        """
        if comment_id is None:
            raise RuntimeError("the resolved finding has no comment id to rewrite")
        rewritten = body.replace(_ACTIVE_MARKER_PREFIX, _RESOLVED_MARKER_PREFIX).replace(
            _ACTIVE_IDENTITY_PREFIX, _RESOLVED_IDENTITY_PREFIX
        )
        self._update_review_comment(comment_id, rewritten)

    def _restore_comment_active(self, comment_id: int | None, body: str) -> None:
        """Restore the original active marker body after resolution fails."""
        if comment_id is None:
            return
        self._update_review_comment(comment_id, body)

    def _update_review_comment(self, comment_id: int, body: str) -> None:
        """Replace one review comment body, raising on any GitHub failure."""
        url = f"{self._api}/pulls/comments/{comment_id}"
        resp = self._client.patch(
            url,
            headers=self._json_headers,
            json={"body": body},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Feedback learning (adapter-only, beyond the frozen port): read the 👎
    # reactions on our finding comments so a later run can suppress them.
    # ------------------------------------------------------------------

    def list_downvoted_fingerprints(self) -> set[str]:
        """Fingerprints of our findings an authorised reviewer reacted 👎 to.

        A 👎 from a user with write access on one of our inline finding comments
        is a signal to stop surfacing that finding — the next run suppresses it.
        Reads, per review thread, the first comment's body and the *users* who
        left a ``THUMBS_DOWN`` reaction: a thread whose opening comment carries
        one of our hidden finding markers AND was downvoted by at least one
        write-access user contributes its fingerprint. On a public repo anyone
        can react, so an unprivileged 👎 is ignored (see ``_has_repo_write``).

        The 👎 reaction is the ONLY learning signal — a resolved thread already
        means "fixed" (handled by resolve-on-fix), never a suppress. Reads only
        reactions and our own markers; PR content is never executed. High and
        critical security findings are never suppressed this way (enforced at
        suppression time), so a downvote can't hide a serious vulnerability.
        """
        downvoted: set[str] = set()
        for node in self._walk_review_threads(
            "comments(first:1){ nodes{ body "
            "reactions(content: THUMBS_DOWN, first: 50){ nodes{ user{ login } } } } }"
        ):
            first = _first_comment(node)
            match = FINDING_MARKER.search(first.get("body", "") or "")
            if match is None:
                continue  # not one of ours (or a thread with no comments)
            reactors = (first.get("reactions") or {}).get("nodes") or []
            logins: set[str] = set()
            for reaction in reactors:
                user = reaction.get("user") or {}
                login = user.get("login")
                if login:
                    logins.add(login)
            # Only an authorised (write+) reviewer's 👎 counts — on a public
            # repo anyone can react, and an unprivileged reaction must never
            # suppress a finding.
            if any(self._has_repo_write(login) for login in logins):
                downvoted.add(match.group(1))
        return downvoted

    def _has_repo_write(self, login: str) -> bool:
        """Whether *login* has write (or higher) access to the repo.

        Feedback learning only trusts a 👎 from someone who can push. Fails
        closed — any lookup error (including a 404 for a non-collaborator) means
        "not authorised", so an unverifiable reactor never suppresses a finding.
        Cached per run since a PR's downvoters repeat across threads.
        """
        cached = self._perm_cache.get(login)
        if cached is not None:
            return cached
        allowed = False
        try:
            resp = self._client.get(
                f"{self._api}/collaborators/{login}/permission",
                headers=self._json_headers,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            # The legacy `permission` field collapses maintain→write, triage→read.
            allowed = resp.json().get("permission") in {"admin", "write"}
        except httpx.HTTPError as exc:
            _log.warning("permission check for %s failed: %s", login, exc)
        self._perm_cache[login] = allowed
        return allowed

    def _graphql(self, query: str, variables: dict[str, Any]) -> Any:
        """Run one GraphQL operation and return its ``data`` (raising on errors)."""
        resp = self._client.post(
            _GRAPHQL_URL,
            headers=self._json_headers,
            json={"query": query, "variables": variables},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL error: {payload['errors']}")
        return payload["data"]

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

        A finding is posted inline only when it is confidently placed
        (``anchored``) AND its ``(path, line, side)`` is a real commentable diff
        line. Anything else — an anchor the engine could not match, or a line
        outside the diff — is demoted rather than posted on a line we can't stand
        behind: a comment on the wrong line breaks trust faster than one without a
        precise line. Inline comments anchor by ``line`` + ``side`` (GitHub's
        recommended params), not the deprecated ``position`` count.

        A ``broad`` finding (reflection tiered it as redesign / infra / contract /
        needs-verification) is routed to its own group even when it would anchor,
        so it renders in the collapsed "Broader observations" block instead of
        cluttering the must-fix inline list.
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
            # Only the position fields are GitHub's; the body is rendered the
            # same way for every host.
            comment: dict[str, Any] = {
                "path": f.path,
                "line": f.line,
                "side": f.side,
                "body": render_inline_body(f),
            }
            inline.append((comment, f))
        return inline, demoted, broad
