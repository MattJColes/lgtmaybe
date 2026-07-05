"""RestGitHubGateway: talks to the GitHub REST API.

Implements GitHubGateway with:
- get_pr_context(): fetches diff + paginated file list + base/head SHAs.
- post_review(): batches inline comments + summary; idempotent via a marker comment.

The httpx.Client is injected so tests can use respx without monkey-patching.
All network calls carry an explicit timeout.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import PRContext, ReviewFinding
from lgtmaybe.core.ports import GitHubGateway

from .checkout import clone_base_tree
from .diff import CommentableLines, build_commentable_lines, is_reviewable

_log = get_logger(__name__)

_TIMEOUT = httpx.Timeout(30.0)
_MARKER = "<!-- lgtmaybe -->"
_GRAPHQL_URL = "https://api.github.com/graphql"

# Hidden marker stamped into every inline comment so a later run can match an
# existing review conversation back to the finding that opened it. The capture
# group is the finding fingerprint.
_FINDING_MARKER = re.compile(r"<!-- lgtmaybe-finding:([0-9a-f]+) -->")

# Hidden marker stamped into the summary review body recording the head SHA
# this review covered, so the next run can review only the commits pushed
# since (commit-scoped incremental review). The capture group is the SHA.
_REVIEWED_MARKER = re.compile(r"<!-- lgtmaybe-reviewed:([0-9a-f]{7,40}) -->")


def finding_fingerprint(path: str, title: str) -> str:
    """Stable short id for a finding's identity (its file and what it flags).

    Used to recognise the same finding across review runs: if a fingerprint that
    opened a conversation is no longer produced, that conversation is a candidate
    to auto-resolve. Only the path and title feed the hash (never model prose),
    so the marker is safe to embed in a comment body verbatim.
    """
    digest = hashlib.sha256(f"{path}\n{title.strip().lower()}".encode())
    return digest.hexdigest()[:12]


# Concurrency for the per-file head-content fetch. The contents are independent
# GETs, so fetching them serially is pure round-trip latency on a many-file PR.
_CONTENT_FETCH_WORKERS = 8

# Link header rel="next" parser
_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')

# Zero-width space, inserted to break up a triple-backtick run so it can't be
# parsed as a Markdown fence delimiter.
_ZWSP = "​"


def _defang_fences(text: str) -> str:
    """Neutralise embedded triple-backticks in model-supplied text (title, body,
    suggestion) so it can't break out of a Markdown fence and inject content
    (e.g. a phishing link) into the rendered comment.

    The diff is attacker-controlled on a fork PR, so a prompt injection that
    survives the guard could steer the model into fence-breaking output. We insert
    zero-width spaces between the backticks: the run no longer reads as a fence,
    while the text stays visually intact.
    """
    return text.replace("```", f"`{_ZWSP}`{_ZWSP}`")


def _render_demoted(demoted: list[ReviewFinding]) -> str:
    """Render findings that couldn't be confidently placed inline as a body section.

    These keep their severity, file, and explanation — only the precise line (and
    its one-click suggestion) is dropped, because we could not anchor it. Returns
    "" when there is nothing to demote, so a normal review's body is unchanged.
    """
    if not demoted:
        return ""
    lines = [
        "",
        "",
        "### Additional findings",
        "",
        "_These relate to the changes but aren't tied to a single line:_",
        "",
    ]
    for f in demoted:
        lines.append(
            f"- **[{f.severity.upper()}] {_defang_fences(f.title)}** "
            f"(`{f.path}`) — {_defang_fences(f.body)}"
        )
    return "\n".join(lines)


def _render_broad(broad: list[ReviewFinding]) -> str:
    """Render broad (redesign / infra / contract / needs-verification) findings.

    These are real findings the reflection pass judged too wide-reaching to action
    on a single line, so they're collapsed into a ``<details>`` block to keep the
    must-fix inline list tight without dropping the observation. Returns "" when
    there is nothing broad, so a normal review's body is unchanged.
    """
    if not broad:
        return ""
    lines = [
        "",
        "",
        "<details><summary>Broader observations</summary>",
        "",
        "_These are wider-reaching — a redesign, an infra/contract change, or one "
        "needing independent verification — so they're collected here rather than "
        "pinned to a line:_",
        "",
    ]
    for f in broad:
        lines.append(
            f"- **[{f.severity.upper()}] {_defang_fences(f.title)}** "
            f"(`{f.path}`) — {_defang_fences(f.body)}"
        )
    lines += ["", "</details>"]
    return "\n".join(lines)


class RestGitHubGateway(GitHubGateway):
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
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        # Scope the idempotency marker to a provider/model so concurrent reviews
        # from different backends on one PR update their own comment instead of
        # clobbering each other. Unkeyed gateways keep the legacy marker.
        self._marker = f"<!-- lgtmaybe:{marker_key} -->" if marker_key else _MARKER
        # Idempotency marker for the describe comment — its own family so a
        # description update never clobbers the review summary (or vice versa).
        self._describe_marker = (
            f"<!-- lgtmaybe-describe:{marker_key} -->"
            if marker_key
            else "<!-- lgtmaybe-describe -->"
        )
        self._resolve_fixed = resolve_fixed
        # Cached PR head SHA for read-only on-demand file fetches (get_file_contents),
        # populated lazily and reused so a deferral recheck doesn't re-fetch metadata.
        self._head_sha: str | None = None
        # Lazily-cloned base tree for ast-grep symbol resolution (cloned at most once,
        # and only if a symbol deferral actually needs it). _done guards the one-shot.
        self._base_root: Path | None = None
        self._base_root_done = False
        # Incremental-review scope: when set (via set_incremental_scope), only
        # this run's reviewed paths. Resolve-on-fix then skips threads on other
        # paths — a finding absent merely because its file wasn't re-reviewed
        # this run must never be spuriously resolved. None = full review.
        self._incremental_paths: set[str] | None = None
        # Head SHA this run actually reviewed (set via mark_reviewed by the
        # orchestrator on the success path only). Drives the reviewed-SHA stamp
        # and re-run inline-comment posting. Deliberately NOT inferred inside
        # post_review: a failure notice posts through the same method and must
        # never record "reviewed up to head" — the next run would then skip
        # commits nobody reviewed.
        self._reviewed_sha: str | None = None

    # ------------------------------------------------------------------
    # GitHubGateway implementation
    # ------------------------------------------------------------------

    def get_pr_context(self) -> PRContext:
        """Fetch PR metadata, unified diff, and the full paginated files list."""
        pr_url = f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}"

        # Fetch metadata (base/head SHAs)
        meta = self._get_json(pr_url)
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
        diff_headers = {**self._headers, "Accept": "application/vnd.github.v3.diff"}
        resp = self._client.get(pr_url, headers=diff_headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        diff: str = resp.text

        # Fetch paginated files list
        files_url = (
            f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}/files?per_page=100"
        )
        changed_files = self._fetch_all_files(files_url)

        # Fetch head-revision text of reviewable files so the engine can pad hunks
        # with surrounding context. Read-only API fetch — never a checkout — and
        # the engine redacts it before it leaves the process. The fetches are
        # independent, so run them concurrently to cut round-trip latency.
        reviewable = [path for path in changed_files if is_reviewable(path)]
        file_contents: dict[str, str] = {}
        if reviewable:
            workers = min(_CONTENT_FETCH_WORKERS, len(reviewable))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = pool.map(lambda p: (p, self._get_file_content(p, head_sha)), reviewable)
                file_contents = {path: content for path, content in results if content is not None}

        return PRContext(
            diff=diff,
            changed_files=changed_files,
            base_sha=base_sha,
            head_sha=head_sha,
            repo=self._repo,
            pr_number=self._pr_number,
            file_contents=file_contents,
            title=meta.get("title") or "",
            description=meta.get("body") or "",
            commit_messages=self._fetch_commit_subjects(),
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

        The ``diff`` parameter is optional; when omitted the method fetches the
        PR diff to build the commentable-line index.
        """
        if diff is None:
            ctx = self.get_pr_context()
            diff = ctx.diff

        commentable: CommentableLines = build_commentable_lines(diff)
        comments, demoted, broad = self._partition_findings(findings, commentable)

        body = f"{summary}{_render_demoted(demoted)}{_render_broad(broad)}\n\n{self._marker}"
        if self._reviewed_sha:
            # Record how far this review got, so the next run can review only
            # the commits pushed since (incremental review). Only stamped when
            # the orchestrator marked this run as a completed review — a
            # failure notice must not move the incremental watermark (and by
            # replacing the body it clears any stale stamp, so the next run
            # safely falls back to a full review).
            body += f"\n<!-- lgtmaybe-reviewed:{self._reviewed_sha} -->"
        existing_id = self._find_existing_review()

        reviews_url = f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}/reviews"

        if existing_id is not None:
            # Update the existing review body (inline comments cannot be changed
            # through this endpoint, but the summary is updated).
            update_url = f"{reviews_url}/{existing_id}"
            resp = self._client.put(
                update_url,
                headers={**self._headers, "Accept": "application/vnd.github+json"},
                json={"body": body},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            # The review-update endpoint can't add inline comments, so post the
            # NEW findings (fingerprints not already on the PR) as individual
            # review comments — otherwise a re-run's fresh findings would only
            # ever appear in the summary body, silently losing their line.
            self._post_new_inline_comments(comments, self._reviewed_sha)
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
                headers={**self._headers, "Accept": "application/vnd.github+json"},
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
        url = f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}"
        if self._head_sha is None:
            try:
                self._head_sha = self._get_json(url)["head"]["sha"]
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

    def _get_base_ref(self) -> str | None:
        """The PR's base branch name (e.g. ``main``), or None if it can't be fetched."""
        url = f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}"
        try:
            ref = self._get_json(url)["base"]["ref"]
        except (httpx.HTTPError, KeyError, TypeError):
            return None
        return ref if isinstance(ref, str) and ref else None

    def post_issue_comment(self, body: str) -> None:
        """Post a standalone comment to the PR conversation (in-thread reply).

        Used by slash commands (/ask, /describe). Beyond the frozen GitHubGateway
        port, which only models reviews.
        """
        url = f"https://api.github.com/repos/{self._repo}/issues/{self._pr_number}/comments"
        resp = self._client.post(
            url,
            headers={**self._headers, "Accept": "application/vnd.github+json"},
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
        stamped = f"{body}\n\n{self._describe_marker}"
        url = f"https://api.github.com/repos/{self._repo}/issues/{self._pr_number}/comments"
        for resp in self._paginate(f"{url}?per_page=100"):
            for comment in resp.json():
                if self._describe_marker in (comment.get("body") or ""):
                    edit_url = (
                        f"https://api.github.com/repos/{self._repo}/issues/comments/{comment['id']}"
                    )
                    patched = self._client.patch(
                        edit_url,
                        headers={**self._headers, "Accept": "application/vnd.github+json"},
                        json={"body": stamped},
                        timeout=_TIMEOUT,
                    )
                    patched.raise_for_status()
                    return
        created = self._client.post(
            url,
            headers={**self._headers, "Accept": "application/vnd.github+json"},
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
            base = f"https://api.github.com/repos/{self._repo}/issues/{self._pr_number}"
            current: set[str] = {
                item["name"]
                for resp in self._paginate(f"{base}/labels?per_page=100")
                for item in resp.json()
            }
            managed = {
                name
                for name in current
                if name.startswith("review-effort/")
                or name in ("possible-security-issue", "consider-splitting")
            }
            for stale in sorted(managed - set(labels)):
                # The label name is a single path segment — quote it fully, or
                # the slash in "review-effort/2" would read as a path separator.
                resp = self._client.delete(
                    f"{base}/labels/{quote(stale, safe='')}",
                    headers={**self._headers, "Accept": "application/vnd.github+json"},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
            to_add = sorted(set(labels) - current)
            if to_add:
                resp = self._client.post(
                    f"{base}/labels",
                    headers={**self._headers, "Accept": "application/vnd.github+json"},
                    json={"labels": to_add},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — labels are auxiliary, never fatal
            _log.warning("applying PR labels failed: %s", exc)

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

    def compare_diff(self, base_sha: str, head_sha: str) -> str | None:
        """Unified diff of the commits between *base_sha* and *head_sha*, or None.

        Uses the compare API (read-only — never a checkout). Returns the diff
        only when head is strictly **ahead** of the last-reviewed SHA (a normal
        push). A force-push/rebase (``diverged``/``behind``), an ``identical``
        compare, and any API failure (e.g. a GC'd SHA 404ing after a
        force-push) all return None — the caller falls back to a full review
        rather than trusting a meaningless increment.
        """
        url = f"https://api.github.com/repos/{self._repo}/compare/{base_sha}...{head_sha}"
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

    def set_incremental_scope(self, paths: set[str] | None) -> None:
        """Restrict resolve-on-fix to threads on *paths* (None = no restriction).

        Set by the incremental-review path with the files actually re-reviewed
        this run: a finding on any other file is absent from this run's
        findings only because its hunks weren't in the increment, so its
        conversation must stay open rather than be spuriously resolved.
        """
        self._incremental_paths = paths

    def mark_reviewed(self, head_sha: str) -> None:
        """Declare that this run is a completed review of *head_sha*.

        Called by the orchestrator on the success path, just before
        ``post_review``. Enables the reviewed-SHA stamp (the incremental
        watermark) and re-run inline-comment posting. Never called for a
        failure notice — a failed run must not move the watermark.
        """
        self._reviewed_sha = head_sha

    def _post_new_inline_comments(
        self, comments: list[dict[str, Any]], head_sha: str | None
    ) -> None:
        """Post the inline comments whose finding isn't already on the PR.

        The review-update endpoint only replaces the body, so on a re-run new
        findings are posted as individual review comments (anchored to
        *head_sha*). Each comment body already carries its hidden fingerprint;
        comments whose fingerprint is already present on the PR are skipped, so
        a re-run never duplicates an open conversation. Best-effort as a whole
        — without a head SHA there is nothing to anchor to, so nothing posts.
        """
        if not comments or head_sha is None:
            return
        existing = self._existing_finding_fingerprints()
        url = f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}/comments"
        for comment in comments:
            match = _FINDING_MARKER.search(comment.get("body", ""))
            if match is not None and match.group(1) in existing:
                continue
            resp = self._client.post(
                url,
                headers={**self._headers, "Accept": "application/vnd.github+json"},
                json={**comment, "commit_id": head_sha},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()

    def _existing_finding_fingerprints(self) -> set[str]:
        """Fingerprints of every lgtmaybe finding already posted inline on the PR."""
        url = (
            f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}"
            "/comments?per_page=100"
        )
        fingerprints: set[str] = set()
        for resp in self._paginate(url):
            for item in resp.json():
                match = _FINDING_MARKER.search(item.get("body", "") or "")
                if match is not None:
                    fingerprints.add(match.group(1))
        return fingerprints

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_json(self, url: str) -> Any:
        resp = self._client.get(
            url,
            headers={**self._headers, "Accept": "application/vnd.github+json"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _get_file_content(self, path: str, ref: str) -> str | None:
        """Return the raw text of *path* at *ref*, or None if it can't be fetched.

        Deleted/renamed-away files (404) and any other fetch error degrade to
        None so the engine simply reviews the bare diff for that file.
        """
        url = f"https://api.github.com/repos/{self._repo}/contents/{path}?ref={ref}"
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
        url = (
            f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}"
            "/commits?per_page=100"
        )
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

    def _fetch_all_files(self, first_url: str) -> list[str]:
        """Follow Link rel=next pagination and collect all filenames."""
        files: list[str] = []
        for resp in self._paginate(first_url):
            for item in resp.json():
                files.append(item["filename"])
        return files

    @staticmethod
    def _next_link(resp: httpx.Response) -> str | None:
        link = resp.headers.get("Link", "")
        m = _LINK_NEXT.search(link)
        return m.group(1) if m else None

    def _paginate(self, url: str) -> Iterator[httpx.Response]:
        next_url: str | None = url
        while next_url is not None:
            resp = self._client.get(
                next_url,
                headers={**self._headers, "Accept": "application/vnd.github+json"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            yield resp
            next_url = self._next_link(resp)

    def _find_existing_review(self) -> int | None:
        """Return the ID of the first review whose body contains the marker, or None."""
        entry = self._find_existing_review_entry()
        return entry[0] if entry is not None else None

    def _find_existing_review_entry(self) -> tuple[int, str] | None:
        """Return ``(id, body)`` of the first review carrying our marker, or None.

        Follows Link rel=next pagination — a busy PR can hold more than one page
        of reviews, and missing the marker there would duplicate the review
        instead of updating it.
        """
        url = f"https://api.github.com/repos/{self._repo}/pulls/{self._pr_number}/reviews"
        for resp in self._paginate(url):
            for review in resp.json():
                body: str = review.get("body", "") or ""
                if self._marker in body:
                    review_id: int = review["id"]
                    return review_id, body
        return None

    # ------------------------------------------------------------------
    # Auto-resolve fixed conversations (GraphQL — the REST review API can't
    # resolve a review thread).
    # ------------------------------------------------------------------

    def _resolve_fixed_threads(self, findings: list[ReviewFinding]) -> None:
        """Resolve our prior conversations whose finding is gone and code changed.

        A thread is "fixed" when its hidden fingerprint is no longer produced by
        this run AND GitHub marks it outdated (the lines it anchored to changed).
        Each fixed thread gets a short reply for the audit trail, then collapses.

        Entirely best-effort: any failure is logged and swallowed so an
        auto-resolve hiccup can never fail an otherwise-successful review.
        """
        try:
            current = {finding_fingerprint(f.path, f.title) for f in findings}
            for thread_id in self._fixed_thread_ids(current):
                self._reply_and_resolve(thread_id)
        except Exception as exc:  # noqa: BLE001 — best-effort; never fail the review
            _log.warning("auto-resolve of fixed conversations failed: %s", exc)

    def _fixed_thread_ids(self, current: set[str]) -> list[str]:
        """Thread ids of our unresolved, outdated conversations whose finding is gone."""
        query = """
        query($owner:String!,$name:String!,$number:Int!,$cursor:String){
          repository(owner:$owner,name:$name){
            pullRequest(number:$number){
              reviewThreads(first:100, after:$cursor){
                pageInfo{ hasNextPage endCursor }
                nodes{
                  id
                  isResolved
                  isOutdated
                  path
                  comments(first:1){ nodes{ body } }
                }
              }
            }
          }
        }
        """
        owner, _, name = self._repo.partition("/")
        fixed: list[str] = []
        cursor: str | None = None
        while True:
            data = self._graphql(
                query,
                {"owner": owner, "name": name, "number": self._pr_number, "cursor": cursor},
            )
            conn = data["repository"]["pullRequest"]["reviewThreads"]
            for node in conn["nodes"]:
                if node.get("isResolved"):
                    continue
                if not node.get("isOutdated"):
                    continue
                if (
                    self._incremental_paths is not None
                    and node.get("path") not in self._incremental_paths
                ):
                    # Incremental run: this file wasn't re-reviewed, so its
                    # finding's absence is no evidence it was fixed.
                    continue
                comments = node.get("comments", {}).get("nodes", [])
                first_body = comments[0].get("body", "") if comments else ""
                match = _FINDING_MARKER.search(first_body)
                if match is None:
                    continue  # not one of ours
                if match.group(1) in current:
                    continue  # still flagged this run — leave it open
                fixed.append(node["id"])
            page = conn.get("pageInfo", {})
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
        return fixed

    def _reply_and_resolve(self, thread_id: str) -> None:
        """Post a short reply on a thread, then mark it resolved."""
        reply = """
        mutation($threadId:ID!,$body:String!){
          addPullRequestReviewThreadReply(
            input:{pullRequestReviewThreadId:$threadId, body:$body}
          ){ comment{ id } }
        }
        """
        self._graphql(reply, {"threadId": thread_id, "body": "✅ Looks resolved."})
        resolve = """
        mutation($threadId:ID!){
          resolveReviewThread(input:{threadId:$threadId}){ thread{ id isResolved } }
        }
        """
        self._graphql(resolve, {"threadId": thread_id})

    def _graphql(self, query: str, variables: dict[str, Any]) -> Any:
        """Run one GraphQL operation and return its ``data`` (raising on errors)."""
        resp = self._client.post(
            _GRAPHQL_URL,
            headers={**self._headers, "Accept": "application/vnd.github+json"},
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
    ) -> tuple[list[dict[str, Any]], list[ReviewFinding], list[ReviewFinding]]:
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
        comments: list[dict[str, Any]] = []
        demoted: list[ReviewFinding] = []
        broad: list[ReviewFinding] = []
        for f in findings:
            if f.broad:
                broad.append(f)
                continue
            if not f.anchored or (f.path, f.line, f.side) not in commentable:
                demoted.append(f)
                continue
            comment: dict[str, Any] = {
                "path": f.path,
                "line": f.line,
                "side": f.side,
                "body": f"**[{f.severity.upper()}] {_defang_fences(f.title)}**"
                f"\n\n{_defang_fences(f.body)}",
            }
            if f.suggestion is not None:
                comment["body"] += f"\n\n```suggestion\n{_defang_fences(f.suggestion)}\n```"
            # Stamp a hidden fingerprint so a later run can recognise this
            # conversation and auto-resolve it once the finding is gone.
            fp = finding_fingerprint(f.path, f.title)
            comment["body"] += f"\n\n<!-- lgtmaybe-finding:{fp} -->"
            comments.append(comment)
        return comments, demoted, broad
