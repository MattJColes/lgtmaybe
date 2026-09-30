"""The risk verdict on the review summary: the line's segment and a hidden marker."""

from __future__ import annotations

from pathlib import Path

import pytest

from lgtmaybe.cli import run_review
from lgtmaybe.core.models import PRContext, RiskAssessment, RiskConfig, RiskReason
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine import engine as engine_module
from lgtmaybe.engine.engine import INCOMPLETE_MARKER, LENSES_MARKER_PREFIX
from tests.conftest import make_cfg
from tests.fakes import FakeProvider

_TF_DIFF = (
    "diff --git a/infra/main.tf b/infra/main.tf\n--- a/infra/main.tf\n+++ b/infra/main.tf\n"
    "@@ -1 +1 @@\n-node_count = 6\n+node_count = 3\n"
)
_CTX = PRContext(
    diff=_TF_DIFF,
    changed_files=["infra/main.tf"],
    base_sha="a",
    head_sha="b",
    repo="o/r",
    pr_number=1,
)
_SEGMENT = "risk high (touches infrastructure: `infra/main.tf`)"
_MARKER = "<!-- lgtmaybe-risk:high -->"


def _review(tmp_path: Path, ctx: PRContext = _CTX, *, findings=None, **cfg):  # type: ignore[no-untyped-def]
    provider = FakeProvider() if findings is None else FakeProvider(findings=findings)
    engine = LLMReviewEngine(provider, workspace_root=tmp_path)
    return engine.review(ctx, make_cfg(**cfg))[1]


def test_the_default_summary_line_carries_the_verdict_and_marker(tmp_path: Path) -> None:
    summary = _review(tmp_path)
    assert f"· {_SEGMENT} · provider" in summary
    assert _MARKER in summary
    # The lenses marker still closes the summary, as its own contract promises.
    assert summary.rstrip().endswith("-->")
    assert summary.rstrip().splitlines()[-1].startswith(LENSES_MARKER_PREFIX)


def test_a_clean_review_carries_the_verdict_too(tmp_path: Path) -> None:
    summary = _review(tmp_path, findings=[])
    assert summary.startswith("👍 LGTM!")
    assert _SEGMENT in summary
    assert _MARKER in summary


def test_disabled_risk_adds_nothing(tmp_path: Path) -> None:
    summary = _review(tmp_path, risk=RiskConfig(enabled=False))
    assert "risk " not in summary
    assert "lgtmaybe-risk" not in summary


def test_the_template_placeholder_renders_the_segment(tmp_path: Path) -> None:
    summary = _review(tmp_path, summary_template="{count} findings, {risk}")
    assert f"findings, {_SEGMENT}" in summary


def test_a_template_without_the_placeholder_still_carries_the_marker(tmp_path: Path) -> None:
    summary = _review(tmp_path, summary_template="{count} findings")
    assert "risk high" not in summary
    assert _MARKER in summary


def test_a_verdict_already_on_the_context_is_used_as_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI assesses the whole PR before incremental scoping narrows the diff;
    the engine must report that verdict, not re-assess the narrowed one."""

    def boom(*_a: object, **_k: object) -> RiskAssessment:
        raise AssertionError("re-assessed a context that already carried a verdict")

    monkeypatch.setattr(engine_module, "assess_risk", boom)
    carried = RiskAssessment(
        level="medium",
        reasons=[RiskReason(factor="size", key="size", level="medium", text="600 changed lines")],
    )
    summary = _review(tmp_path, _CTX.model_copy(update={"risk": carried}))
    assert "risk medium (600 changed lines)" in summary
    assert "<!-- lgtmaybe-risk:medium -->" in summary


def test_an_unavailable_verdict_says_so_and_carries_no_marker(tmp_path: Path) -> None:
    ctx = _CTX.model_copy(update={"risk": RiskAssessment(level=None)})
    summary = _review(tmp_path, ctx)
    assert "risk unavailable" in summary
    assert "lgtmaybe-risk" not in summary


def test_risk_marker_cannot_be_mistaken_for_another_marker_family() -> None:
    from lgtmaybe.core.comment import FINDING_MARKER
    from lgtmaybe.github.rest_gateway import _MARKER as SUMMARY_MARKER
    from lgtmaybe.github.rest_gateway import _REVIEWED_MARKER

    for level in ("low", "medium", "high", "critical"):
        marker = f"<!-- lgtmaybe-risk:{level} -->"
        assert SUMMARY_MARKER not in marker
        assert FINDING_MARKER.search(marker) is None
        assert _REVIEWED_MARKER.search(marker) is None
        assert INCOMPLETE_MARKER not in marker
        assert not marker.startswith(LENSES_MARKER_PREFIX)


def test_run_review_assesses_the_whole_pr_before_incremental_scoping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-line follow-up push reports the level of the whole PR."""
    from tests.cli.test_incremental_review import IncrementalFakeGitHub, RecordingEngine

    monkeypatch.chdir(tmp_path)
    big = "".join(f"+line {i}\n" for i in range(600))
    full = f"diff --git a/src/big.py b/src/big.py\n@@ -0,0 +1,600 @@\n{big}"
    inc = "diff --git a/src/big.py b/src/big.py\n@@ -600,0 +601 @@\n+one more\n"
    ctx = _CTX.model_copy(update={"diff": full, "changed_files": ["src/big.py"], "head_sha": "h2"})
    github = IncrementalFakeGitHub(ctx, last_sha="h1", compare_result=inc)
    engine = RecordingEngine()

    run_review(github=github, engine=engine, cfg=make_cfg(incremental=True), dry_run=False)

    reviewed = engine.reviewed_ctxs[0]
    assert reviewed.diff == inc
    assert reviewed.risk is not None
    assert "600 changed lines" in [reason.text for reason in reviewed.risk.reasons]


def test_run_review_skips_the_assessment_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.cli.test_incremental_review import IncrementalFakeGitHub, RecordingEngine

    monkeypatch.chdir(tmp_path)
    engine = RecordingEngine()
    run_review(
        github=IncrementalFakeGitHub(_CTX),
        engine=engine,
        cfg=make_cfg(risk=RiskConfig(enabled=False)),
        dry_run=False,
    )
    assert engine.reviewed_ctxs[0].risk is None
