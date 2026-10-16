"""The Gitea adapter's calls against a real Forgejo: Forgejo is served as Gitea.

Unit tests pin the adapter to Gitea's documented API with mocked responses.
These run the same calls against a live Forgejo, so a divergence in the fork
shows up here before a user finds it. No model is involved.
"""

from __future__ import annotations

import httpx
import pytest

from lgtmaybe.core.models import ReviewFinding
from lgtmaybe.core.ports import SupportsThreadResolution
from lgtmaybe.gitea import GiteaGateway

from .conftest import LOOP_LINE, Forgejo, PullRequest

pytestmark = pytest.mark.forgejo_contract

LOOP = ReviewFinding(
    path="calc.py",
    line=LOOP_LINE,
    severity="high",
    title="Loop reads one past the end",
    body="`range(len(xs) + 1)` indexes past the last element.",
    anchor="    for i in range(len(xs) + 1):",
)
UNANCHORED = ReviewFinding(
    path="calc.py",
    line=1,
    severity="medium",
    title="Unanchored finding",
    body="Its anchor matched no changed line.",
    anchor="no such line",
    anchored=False,
)


def _gateway(forgejo: Forgejo, pr: PullRequest) -> GiteaGateway:
    return GiteaGateway(
        host=forgejo.host,
        repo=f"{forgejo.owner}/{forgejo.repo}",
        pr_number=pr.number,
        token=forgejo.token,
        scheme=forgejo.scheme,
        client=httpx.Client(timeout=30, trust_env=False),
    )


def _summaries(forgejo: Forgejo, pr: PullRequest) -> list[str]:
    return [
        c["body"]
        for c in forgejo.get(f"/issues/{pr.number}/comments")
        if "<!-- lgtmaybe -->" in c["body"]
    ]


def test_context_comes_from_the_api(forgejo: Forgejo, pull_request: PullRequest) -> None:
    ctx = _gateway(forgejo, pull_request).get_pr_context()

    assert ctx.head_sha == pull_request.head_sha
    assert ctx.changed_files == ["calc.py"]
    assert "+    for i in range(len(xs) + 1):" in ctx.diff
    assert ctx.file_contents["calc.py"].startswith("def total(xs):")
    assert ctx.title == "Add total"
    assert ctx.commit_messages == ["feat: add total"]


def test_inline_and_demoted_findings_post(forgejo: Forgejo, pull_request: PullRequest) -> None:
    _gateway(forgejo, pull_request).post_review([LOOP, UNANCHORED], "summary v1")

    (review,) = forgejo.get(f"/pulls/{pull_request.number}/reviews")
    (comment,) = forgejo.get(f"/pulls/{pull_request.number}/reviews/{review['id']}/comments")
    assert comment["path"] == "calc.py"
    assert comment["position"] == LOOP_LINE
    assert "Loop reads one past the end" in comment["body"]
    (summary,) = _summaries(forgejo, pull_request)
    assert summary.startswith("summary v1")
    assert "Unanchored finding" in summary


def test_a_rerun_edits_the_summary_and_skips_posted_findings(
    forgejo: Forgejo, pull_request: PullRequest
) -> None:
    _gateway(forgejo, pull_request).post_review([LOOP], "summary v1")

    _gateway(forgejo, pull_request).post_review([LOOP], "summary v2")

    assert len(forgejo.get(f"/pulls/{pull_request.number}/reviews")) == 1
    (summary,) = _summaries(forgejo, pull_request)
    assert summary.startswith("summary v2")


def test_describe_and_diagram_upsert_in_their_own_families(
    forgejo: Forgejo, pull_request: PullRequest
) -> None:
    gateway = _gateway(forgejo, pull_request)
    gateway.post_review([], "summary")
    gateway.post_describe_comment("describe v1")
    gateway.post_diagram_comment("diagram v1")
    gateway.post_describe_comment("describe v2")

    bodies = [c["body"] for c in forgejo.get(f"/issues/{pull_request.number}/comments")]
    assert len(bodies) == 3
    assert sum(b.startswith("describe v2") for b in bodies) == 1
    assert sum(b.startswith("diagram v1") for b in bodies) == 1


def test_a_commit_status_is_published(forgejo: Forgejo, pull_request: PullRequest) -> None:
    _gateway(forgejo, pull_request).create_check_run(
        pull_request.head_sha, "failure", "1 high finding", "summary"
    )

    statuses = forgejo.get(f"/statuses/{pull_request.head_sha}")
    assert [(s["context"], s["status"]) for s in statuses] == [("lgtmaybe", "failure")]


def test_only_existing_labels_are_applied(forgejo: Forgejo, pull_request: PullRequest) -> None:
    _gateway(forgejo, pull_request).apply_pr_labels(["lgtmaybe:risk-high", "lgtmaybe:absent"])

    labels = forgejo.get(f"/issues/{pull_request.number}/labels")
    assert [label["name"] for label in labels] == ["lgtmaybe:risk-high"]


def test_write_access_decides_who_may_run_a_command(
    forgejo: Forgejo, pull_request: PullRequest
) -> None:
    gateway = _gateway(forgejo, pull_request)

    assert gateway.has_repo_write(forgejo.owner) is True
    assert gateway.has_repo_write(forgejo.writer) is True
    assert gateway.has_repo_write(forgejo.reader) is False


def test_a_conversation_comment_posts_and_lists(
    forgejo: Forgejo, pull_request: PullRequest
) -> None:
    gateway = _gateway(forgejo, pull_request)
    gateway.post_issue_comment("hello from the contract test")

    assert (forgejo.owner, "hello from the contract test") in gateway.list_conversation_comments()


def test_forgejo_still_has_no_api_to_resolve_review_threads(forgejo: Forgejo) -> None:
    """Resolve-on-fix stays unclaimed on Forgejo because the API cannot serve it.

    Forgejo resolves a review conversation only through a web route that needs
    a browser session (an API token is redirected to sign in), and no REST
    route exists in Forgejo 11 to 17. When this fails, a release has added one:
    probe for it then, and claim thread resolution only where it answers.
    """
    resp = forgejo.admin.get(f"{forgejo.url}/swagger.v1.json")
    resp.raise_for_status()
    spec = resp.json()

    routes = [
        f"{method.upper()} {path}"
        for path, operations in spec["paths"].items()
        for method, operation in operations.items()
        if any(
            word
            in f"{path} {operation.get('operationId', '')} {operation.get('summary', '')}".lower()
            for word in ("resolve", "conversation")
        )
    ]
    assert routes == [], f"Forgejo {spec['info']['version']} now exposes {routes}"
    assert not isinstance(_gateway(forgejo, PullRequest(1, "")), SupportsThreadResolution)
