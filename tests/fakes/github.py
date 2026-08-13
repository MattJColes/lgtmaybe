"""FakeGitHub: serves a canned PRContext, records posted reviews."""

from __future__ import annotations

from lgtmaybe.core.models import PRContext, ReviewFinding

_DEFAULT_CTX = PRContext(
    diff="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
    changed_files=["a.py"],
    base_sha="basesha",
    head_sha="headsha",
    repo="lgtmaybe/lgtmaybe",
    pr_number=1,
)


class FakeGitHub:
    """A GitHubGateway backed by in-memory state."""

    def __init__(self, ctx: PRContext | None = None) -> None:
        self._ctx = _DEFAULT_CTX if ctx is None else ctx
        self.posted: list[tuple[list[ReviewFinding], str]] = []
        self.posted_diffs: list[str | None] = []
        self.comments: list[str] = []
        self.described: list[str] = []
        self.diagrams: list[str] = []
        self.check_runs: list[dict[str, str]] = []
        # Resolve-on-fix review-thread replies — beyond the frozen port.
        self.replies: list[tuple[str, str]] = []

    def get_pr_context(self) -> PRContext:
        return self._ctx

    def post_review(
        self, findings: list[ReviewFinding], summary: str, diff: str | None = None
    ) -> None:
        self.posted.append((findings, summary))
        self.posted_diffs.append(diff)

    def post_issue_comment(self, body: str) -> None:
        """In-thread reply — beyond the frozen port, used by slash commands."""
        self.comments.append(body)

    def post_describe_comment(self, body: str) -> None:
        """Idempotent PR-description upsert — beyond the frozen port."""
        self.described.append(body)

    def post_diagram_comment(self, body: str) -> None:
        """Idempotent change-diagram upsert — beyond the frozen port."""
        self.diagrams.append(body)

    def create_check_run(self, head_sha: str, conclusion: str, title: str, summary: str) -> None:
        """Merge-gate Check Run (fail_on) — beyond the frozen port."""
        self.check_runs.append(
            {
                "head_sha": head_sha,
                "conclusion": conclusion,
                "title": title,
                "summary": summary,
            }
        )

    def reply_in_thread(self, thread_id: str, body: str) -> None:
        """Record a resolve-on-fix reply posted to a review thread."""
        self.replies.append((thread_id, body))
