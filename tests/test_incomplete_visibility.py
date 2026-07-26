"""An incomplete review must say so **on the PR**, not only in the Actions log.

The engine has always disclosed a partial run in its summary ("⚠️ N of M review
calls failed … results may be incomplete"). The delivery is what failed: that
summary reaches GitHub only inside the review body, and on a re-run
`post_review` PUTs the body onto the review object the *first* run created — an
older, silently-edited comment that notifies nobody — while this run's new
findings go out as individual review comments, which GitHub wraps in reviews
with no body at all. A half-complete review then looks exactly like a clean one.

So the invariant under test is about visibility, not about the wording: a run
with at least one failed or skipped lens call posts the notice as **new PR
activity**. Same for a run that failed outright — it ran no lens to completion.
"""

from __future__ import annotations

import json

import httpx
import respx

from lgtmaybe.cli import _post_failure, run_review
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.engine import INCOMPLETE_MARKER
from tests.fakes import FakeEngine, FakeGitHub, FakeProvider

_DIFF = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n context\n+new_line = 1\n"
)

_CTX = PRContext(
    diff=_DIFF,
    changed_files=["a.py"],
    base_sha="basesha",
    head_sha="headsha",
    repo="owner/repo",
    pr_number=42,
)

_FINDING = ReviewFinding(
    path="a.py",
    line=2,
    severity=Severity.medium,
    title="Something",
    body="x",
    failure_scenario="When the changed line runs, it misbehaves.",
    anchor="new_line = 1",
)


def _cfg(**overrides: object) -> ReviewConfig:
    base: dict[str, object] = {"provider": Provider.ollama, "model": "m", "reflect": False}
    base.update(overrides)
    return ReviewConfig(**base)  # type: ignore[arg-type]


class _OneLensTimesOut(FakeProvider):
    """Every lens returns one finding except the security lens, which times out.

    The real-world shape of the regression: a couple of slow calls die on the
    per-call timeout while the rest of the fan-out succeeds, so the review
    posts real findings *and* is incomplete.
    """

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        prompt = "\n".join(str(m.get("content", "")) for m in messages).lower()
        if "owasp" in prompt:
            raise TimeoutError("provider request exceeded 60s")
        return ProviderResult(
            text=json.dumps({"findings": [_FINDING.model_dump(mode="json")]}),
            input_tokens=10,
            output_tokens=5,
        )


class _EveryLensSucceeds(FakeProvider):
    """The healthy baseline: every lens answers, nothing to disclose."""

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        return ProviderResult(
            text=json.dumps({"findings": [_FINDING.model_dump(mode="json")]}),
            input_tokens=10,
            output_tokens=5,
        )


def test_failed_lens_call_posts_a_visible_notice() -> None:
    """One timed-out lens ⇒ the incompleteness lands on the PR as its own comment."""
    github = FakeGitHub(_CTX)

    findings, summary = run_review(
        github=github,
        engine=LLMReviewEngine(_OneLensTimesOut()),
        cfg=_cfg(),
        dry_run=False,
    )

    assert findings, "the lenses that did answer must still post their findings"
    assert "results may be incomplete" in summary
    assert github.comments, "an incomplete run must post a visible notice, not only a review body"
    assert "results may be incomplete" in github.comments[0]


def test_complete_run_posts_no_notice_comment() -> None:
    """No failed call ⇒ no extra comment. The notice is a signal, not a habit."""
    github = FakeGitHub(_CTX)

    _, summary = run_review(
        github=github,
        engine=LLMReviewEngine(_EveryLensSucceeds()),
        cfg=_cfg(),
        dry_run=False,
    )

    assert INCOMPLETE_MARKER not in summary
    assert github.comments == []


def test_dry_run_posts_nothing_at_all() -> None:
    """--dry-run stays read-only, incomplete or not."""
    github = FakeGitHub(_CTX)

    run_review(
        github=github,
        engine=LLMReviewEngine(_OneLensTimesOut()),
        cfg=_cfg(),
        dry_run=True,
    )

    assert github.posted == []
    assert github.comments == []


def test_engine_marks_an_incomplete_summary_machine_readably() -> None:
    """The marker — not the prose — is what the posting step keys on, so a
    `summary_template` restyling can never silence the disclosure."""
    engine = LLMReviewEngine(_OneLensTimesOut())

    _, summary = engine.review(_CTX, _cfg(summary_template="{count} findings"))

    assert INCOMPLETE_MARKER in summary


def test_total_failure_notice_is_visible() -> None:
    """A run that failed outright ran no lens to completion — same invariant.

    ``_post_failure`` posts through ``post_review``, which on a re-run only
    edits the older review's body, so the failure needs its own visible comment.
    """
    github = FakeGitHub(_CTX)

    _post_failure(github, RuntimeError("provider quota exhausted"))

    assert github.comments, "a failed review must be visible on the PR"
    assert "provider quota exhausted" in github.comments[0]


# ---------------------------------------------------------------------------
# The reported symptom, end to end against the real gateway
# ---------------------------------------------------------------------------

_REPO = "owner/repo"
_PR = 42
_BASE = "https://api.github.com"
_REVIEWS_URL = f"{_BASE}/repos/{_REPO}/pulls/{_PR}/reviews"
_ISSUE_COMMENTS_URL = f"{_BASE}/repos/{_REPO}/issues/{_PR}/comments"
_MARKER = "<!-- lgtmaybe -->"


class _IncompleteEngine(FakeEngine):
    """Returns the summary an incomplete run produces, marker and all."""

    def review(self, ctx: PRContext, cfg: ReviewConfig):  # type: ignore[override]
        summary = (
            "⚠️ 2 of 8 review calls failed (TimeoutError: provider request "
            "exceeded 60s); results may be incomplete."
            f"\n\n1 finding · provider {cfg.provider.value} · model {cfg.model}"
            f"\n{INCOMPLETE_MARKER}"
        )
        return [_FINDING], summary


@respx.mock
def test_notice_is_visible_on_a_re_run() -> None:
    """The regression: a re-run only PUTs the old review's body, so without the
    comment the PR shows nothing but bodiless one-comment reviews."""
    respx.route(method="GET", url=_REVIEWS_URL).mock(
        return_value=httpx.Response(200, json=[{"id": 99, "body": f"Old summary {_MARKER}"}])
    )
    put_bodies: list[str] = []
    respx.route(method="PUT", url=f"{_REVIEWS_URL}/99").mock(
        side_effect=lambda request: (
            put_bodies.append(json.loads(request.content).get("body", "")),
            httpx.Response(200, json={"id": 99}),
        )[1]
    )
    # No inline comments already on the PR, and posting one succeeds.
    respx.route(method="GET", url__startswith=f"{_BASE}/repos/{_REPO}/pulls/{_PR}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.route(method="POST", url=f"{_BASE}/repos/{_REPO}/pulls/{_PR}/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    posted_comments: list[str] = []
    respx.route(method="POST", url=_ISSUE_COMMENTS_URL).mock(
        side_effect=lambda request: (
            posted_comments.append(json.loads(request.content).get("body", "")),
            httpx.Response(201, json={"id": 2}),
        )[1]
    )

    from lgtmaybe.github import RestGitHubGateway

    gateway = RestGitHubGateway(
        repo=_REPO, pr_number=_PR, token="ghp_test", client=httpx.Client(), resolve_fixed=False
    )

    run_review(
        github=gateway,
        engine=_IncompleteEngine(FakeProvider()),
        cfg=_cfg(incremental=False),
        dry_run=False,
        ctx=_CTX,
    )

    assert put_bodies and "results may be incomplete" in put_bodies[0]
    assert posted_comments, (
        "the in-place body edit is invisible on a re-run — the notice must also "
        "post as new PR activity"
    )
    assert "results may be incomplete" in posted_comments[0]


def test_incomplete_marker_cannot_be_mistaken_for_another_marker_family() -> None:
    """Markers are matched by substring/regex elsewhere; this one must stay disjoint,
    or a summary that discloses incompleteness could be mistaken for the review's
    idempotency marker, a finding fingerprint, or the reviewed-SHA watermark."""
    from lgtmaybe.github.rest_gateway import _FINDING_MARKER, _REVIEWED_MARKER
    from lgtmaybe.github.rest_gateway import _MARKER as _SUMMARY_MARKER

    assert _SUMMARY_MARKER not in INCOMPLETE_MARKER
    assert _FINDING_MARKER.search(INCOMPLETE_MARKER) is None
    assert _REVIEWED_MARKER.search(INCOMPLETE_MARKER) is None
