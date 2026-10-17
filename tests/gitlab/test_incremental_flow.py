"""run_review driving the real GitLab adapter through an incremental re-run.

The adapter tests pin each method; these pin the seams between them as the CLI
uses them: the watermark read, the compare, the follow-up validation that needs
an "outdated" signal GitLab's API does not expose, the resolve scope, and the
watermark write — including that a failed or partial run never moves it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from lgtmaybe.cli import _post_failure
from lgtmaybe.core.models import PRContext, ProviderResult, ReviewConfig, ReviewFinding
from lgtmaybe.engine import INCOMPLETE_MARKER
from lgtmaybe.gitlab import GitLabGateway
from lgtmaybe.review import run_review
from tests.conftest import make_cfg
from tests.fakes import FakeProvider

HOST = "gitlab.example.com"
PROJECT = "group%2Fsub%2Fproject"
ROOT = f"https://{HOST}/api/v4"
API = f"{ROOT}/projects/{PROJECT}"
MR_URL = f"{API}/merge_requests/7"

LAST = "a" * 40
HEAD = "b" * 40
BOT = {"id": 99, "username": "lgtmaybe-bot"}

FULL_DIFF = (
    "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
    "@@ -1,3 +1,3 @@\n a\n-b\n+c\n d\n"
    "diff --git a/other.py b/other.py\n--- a/other.py\n+++ b/other.py\n"
    "@@ -1,3 +1,3 @@\n a\n-b\n+c\n d\n"
)
INCREMENT_HUNK = "@@ -1,3 +1,3 @@\n a\n-b\n+c\n d\n"


class RecordingEngine:
    def __init__(self, summary: str = "0 findings · summary") -> None:
        self.summary = summary
        self.diffs: list[str] = []

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        self.diffs.append(ctx.diff)
        return [], self.summary


def _discussion(thread_id: str, path: str, fingerprint: str) -> dict[str, Any]:
    return {
        "id": thread_id,
        "notes": [
            {
                "id": 100 + len(path),
                "body": f"old finding\n<!-- lgtmaybe-finding:{fingerprint} -->",
                "resolved": False,
                "position": {"new_path": path, "new_line": 2, "head_sha": LAST},
                "author": BOT,
            }
        ],
    }


def _stub_mr(*, commits: list[dict[str, Any]] | None = None, notes_sha: str = LAST):
    respx.get(MR_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "t",
                "description": "",
                "source_branch": "feature",
                "diff_refs": {"base_sha": "1" * 40, "head_sha": HEAD, "start_sha": "1" * 40},
            },
        )
    )
    respx.get(f"{MR_URL}/raw_diffs").mock(return_value=httpx.Response(200, text=FULL_DIFF))
    respx.route(method="GET", url__startswith=f"{MR_URL}/diffs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"old_path": "app.py", "new_path": "app.py"},
                {"old_path": "other.py", "new_path": "other.py"},
            ],
        )
    )
    respx.route(method="GET", url__startswith=f"{MR_URL}/commits").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.route(method="GET", url__startswith=f"{API}/repository/files/").mock(
        return_value=httpx.Response(404)
    )
    respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
        return_value=httpx.Response(
            200,
            json=[
                _discussion("thread-app", "app.py", "a" * 12),
                _discussion("thread-other", "other.py", "b" * 12),
            ],
        )
    )
    respx.route(method="GET", url__startswith=f"{MR_URL}/notes").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 55,
                    "body": f"summary\n\n<!-- lgtmaybe -->\n<!-- lgtmaybe-reviewed:{notes_sha} -->",
                    "author": BOT,
                }
            ],
        )
    )
    respx.get(f"{ROOT}/user").mock(return_value=httpx.Response(200, json=BOT))
    linear = [{"id": HEAD, "parent_ids": [LAST]}]
    compare = respx.route(method="GET", url__startswith=f"{API}/repository/compare").mock(
        return_value=httpx.Response(
            200,
            json={
                "commits": linear if commits is None else commits,
                "diffs": [
                    {
                        "old_path": "app.py",
                        "new_path": "app.py",
                        "a_mode": "100644",
                        "b_mode": "100644",
                        "diff": INCREMENT_HUNK,
                    }
                ],
                "compare_timeout": False,
                "compare_same_ref": False,
            },
        )
    )
    summary = respx.put(f"{MR_URL}/notes/55").mock(return_value=httpx.Response(200, json={}))
    note = respx.post(f"{MR_URL}/notes").mock(return_value=httpx.Response(201, json={}))
    resolves = {}
    for thread in ("thread-app", "thread-other"):
        respx.post(f"{MR_URL}/discussions/{thread}/notes").mock(
            return_value=httpx.Response(201, json={})
        )
        resolves[thread] = respx.put(f"{MR_URL}/discussions/{thread}").mock(
            return_value=httpx.Response(200, json={})
        )
    return {"compare": compare, "summary": summary, "note": note, **resolves}


def _gateway() -> GitLabGateway:
    return GitLabGateway(
        host=HOST, repo="group/sub/project", pr_number=7, token="glpat", client=httpx.Client()
    )


def _cfg() -> ReviewConfig:
    return make_cfg(incremental=True, risk={"enabled": False})


def _both_fixed() -> FakeProvider:
    """A model that claims every earlier finding is fixed — the gate must still hold."""
    return FakeProvider(
        result=ProviderResult(
            text=json.dumps(
                {
                    "verdicts": [
                        {"thread_id": t, "status": "fixed", "reason": "gone"}
                        for t in ("thread-app", "thread-other")
                    ]
                }
            ),
            input_tokens=1,
            output_tokens=1,
        )
    )


def _summary_body(route: respx.Route) -> str:
    return str(json.loads(route.calls[-1].request.content)["body"])


@respx.mock
def test_a_follow_up_push_reviews_only_the_increment_and_resolves_only_what_it_touched() -> None:
    routes = _stub_mr()
    engine = RecordingEngine()

    _findings, summary = run_review(
        github=_gateway(), engine=engine, cfg=_cfg(), dry_run=False, provider=_both_fixed()
    )

    assert len(engine.diffs) == 1
    assert "diff --git a/app.py b/app.py" in engine.diffs[0]
    assert "other.py" not in engine.diffs[0], "only the increment was reviewed"
    assert "Incremental review of the changes since aaaaaaa" in summary
    assert routes["thread-app"].called, "outdated, re-reviewed, validated fixed"
    assert not routes["thread-other"].called, "its file was untouched, so no evidence"
    assert _summary_body(routes["summary"]).endswith(f"<!-- lgtmaybe-reviewed:{HEAD} -->")


@respx.mock
def test_a_force_push_falls_back_to_a_full_review() -> None:
    routes = _stub_mr(commits=[{"id": HEAD, "parent_ids": ["d" * 40]}])
    engine = RecordingEngine()

    _findings, summary = run_review(
        github=_gateway(), engine=engine, cfg=_cfg(), dry_run=False, provider=_both_fixed()
    )

    assert engine.diffs == [FULL_DIFF]
    assert "Incremental review" not in summary
    assert not routes["thread-app"].called, "a full review installs no validated allowlist"
    assert _summary_body(routes["summary"]).endswith(f"<!-- lgtmaybe-reviewed:{HEAD} -->")


@respx.mock
def test_the_completed_head_running_again_writes_nothing() -> None:
    routes = _stub_mr(notes_sha=HEAD)
    engine = RecordingEngine()

    run_review(github=_gateway(), engine=engine, cfg=_cfg(), dry_run=False)

    assert engine.diffs == []
    assert not routes["summary"].called
    assert not routes["compare"].called


@respx.mock
def test_a_partial_run_keeps_the_old_watermark() -> None:
    routes = _stub_mr()
    engine = RecordingEngine(summary=f"partial\n{INCOMPLETE_MARKER}")

    run_review(github=_gateway(), engine=engine, cfg=_cfg(), dry_run=False)

    body = _summary_body(routes["summary"])
    assert body.endswith(f"<!-- lgtmaybe-reviewed:{LAST} -->")
    assert HEAD not in body


@respx.mock
def test_a_failure_notice_never_moves_the_watermark() -> None:
    routes = _stub_mr()
    gateway = _gateway()

    _post_failure(gateway, RuntimeError("boom"))

    # The completed summary at LAST is the record of what WAS reviewed (its
    # demoted findings and watermark), so the notice never overwrites it; a
    # note of its own discloses the failure instead.
    assert not routes["summary"].called
    notice = _summary_body(routes["note"])
    assert "review failed" in notice
    assert "lgtmaybe-reviewed" not in notice


@respx.mock
def test_incremental_off_never_reads_the_watermark() -> None:
    routes = _stub_mr()
    engine = RecordingEngine()

    run_review(
        github=_gateway(),
        engine=engine,
        cfg=make_cfg(incremental=False, risk={"enabled": False}),
        dry_run=False,
    )

    assert engine.diffs == [FULL_DIFF]
    assert not routes["compare"].called
