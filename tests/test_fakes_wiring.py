"""End-to-end wiring on fakes — the gate that unblocks parallel track work."""

from __future__ import annotations

from lgtmaybe.core.models import Provider, ReviewConfig
from tests.fakes import submission

from .fakes import FakeEngine, FakeGitHub, FakeProvider


def test_end_to_end_on_fakes() -> None:
    provider = FakeProvider()
    github = FakeGitHub()
    engine = FakeEngine(provider)  # dependency injection

    ctx = github.get_pr_context()
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    findings, summary = engine.review(ctx, cfg)
    github.post_review(submission(findings, summary))

    # provider was actually invoked with the diff
    assert provider.calls
    assert provider.calls[0]["model"] == "llama3"

    # the review was posted, unchanged, exactly once
    assert len(github.posted) == 1
    posted_findings, posted_summary = github.posted[0]
    assert posted_findings == findings
    assert posted_summary == summary
    assert "findings" in posted_summary
