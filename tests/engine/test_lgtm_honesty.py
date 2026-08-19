"""A clean review must not claim a clean PR when business is still outstanding.

"👍 LGTM!" reads as "this PR is fine". It is only honest when the run actually
found nothing AND nothing is being hidden from the count. Two things break that
without changing the finding count:

- findings this run **suppressed** (an ignore fingerprint, an inline pragma, or a
  👎 an authorised reviewer left last run) — the model flagged them, they simply
  are not shown;
- findings from **earlier runs whose conversations are still open** — an
  incremental run may not even re-review their files, so they never reappear in
  this run's count while still being unaddressed on the PR.

Both route through the engine's existing `notices` list, which already exists to
stop a clean bill of health being claimed on incomplete evidence — and which
short-circuits the LGTM branch by construction.
"""

from __future__ import annotations

import json

from lgtmaybe.core.models import (
    PRContext,
    ProviderResult,
    ReviewFinding,
    Severity,
)
from lgtmaybe.engine import LLMReviewEngine
from tests.conftest import make_cfg
from tests.fakes import FakeProvider

_DIFF = "diff --git a/a.py b/a.py\n@@ -1,1 +1,2 @@\n context\n+new_line = 1\n"


def _ctx(**overrides) -> PRContext:
    base = dict(
        diff=_DIFF,
        changed_files=["a.py"],
        base_sha="a",
        head_sha="b",
        repo="o/r",
        pr_number=1,
    )
    base.update(overrides)
    return PRContext(**base)  # type: ignore[arg-type]


class _Clean(FakeProvider):
    """Finds nothing, every lens — the genuinely-clean baseline."""

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)


class _Flags(FakeProvider):
    """Returns one finding on the changed line, every lens."""

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        finding = ReviewFinding(
            path="a.py",
            line=2,
            severity=Severity.medium,
            title="Something",
            body="x",
            failure_scenario="When the changed line runs, it misbehaves.",
            anchor="new_line = 1",
        )
        return ProviderResult(
            text=json.dumps({"findings": [finding.model_dump(mode="json")]}),
            input_tokens=1,
            output_tokens=1,
        )


class TestSuppressedFindingsAreDisclosed:
    def test_a_downvote_suppressed_finding_blocks_lgtm(self) -> None:
        """learn_feedback drops a 👎'd finding before posting. Reporting the
        resulting zero as LGTM would let a downvote silently convert a real
        finding into a clean bill of health."""
        from lgtmaybe.core.findings import finding_fingerprint

        fp = finding_fingerprint("a.py", "Something")
        findings, summary = LLMReviewEngine(_Flags()).review(
            _ctx(feedback_downvotes=frozenset({fp})), make_cfg()
        )

        assert findings == []
        assert "LGTM" not in summary
        assert "suppress" in summary.lower()

    def test_an_ignored_fingerprint_blocks_lgtm(self) -> None:
        from lgtmaybe.core.findings import finding_fingerprint

        fp = finding_fingerprint("a.py", "Something")
        _findings, summary = LLMReviewEngine(_Flags()).review(
            _ctx(), make_cfg(ignore_fingerprints=[fp])
        )

        assert "LGTM" not in summary
        assert "suppress" in summary.lower()

    def test_a_genuinely_clean_review_still_says_lgtm(self) -> None:
        """The guard must not cost every clean PR its thumbs-up."""
        _findings, summary = LLMReviewEngine(_Clean()).review(_ctx(), make_cfg())
        assert "LGTM" in summary


class TestOpenPriorFindingsAreDisclosed:
    def test_open_earlier_conversations_block_lgtm(self) -> None:
        """Nothing new this run, but earlier findings are still unaddressed —
        an incremental run may not even have re-reviewed their files."""
        _findings, summary = LLMReviewEngine(_Clean()).review(
            _ctx(open_finding_threads=3), make_cfg()
        )

        assert "LGTM" not in summary
        assert "3" in summary

    def test_open_conversations_are_disclosed_alongside_new_findings(self) -> None:
        """Not only on the clean path: a run that finds two new things while five
        remain open should say so."""
        _findings, summary = LLMReviewEngine(_Flags()).review(
            _ctx(open_finding_threads=5), make_cfg(min_severity="info")
        )

        assert "5" in summary

    def test_no_open_conversations_is_silent(self) -> None:
        _findings, summary = LLMReviewEngine(_Clean()).review(
            _ctx(open_finding_threads=0), make_cfg()
        )
        assert "LGTM" in summary
        assert "unresolved" not in summary
