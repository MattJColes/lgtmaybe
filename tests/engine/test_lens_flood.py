"""Tests for the per-lens finding bound (max_findings_per_lens)."""

from __future__ import annotations

import json

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.engine import LLMReviewEngine
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+new line\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)


def _flood(count: int) -> str:
    """One lens response carrying *count* distinct findings.

    The (path, line) pairs are distinct on purpose. That is what the observed
    flood looked like, and why `_dedupe`, which keys on location, collapsed none
    of it.
    """
    return json.dumps(
        [
            {
                "path": "a.py",
                "line": i + 1,
                "severity": "medium",
                "title": f"finding {i}",
                "body": f"body {i}",
                "failure_scenario": f"When line {i + 1} runs, the operation fails.",
            }
            for i in range(count)
        ]
    )


class _FloodingProvider(FakeProvider):
    def __init__(self, count: int) -> None:
        super().__init__()
        self._count = count

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        return ProviderResult(text=_flood(self._count), input_tokens=1, output_tokens=1)


def _cfg(**overrides: object) -> ReviewConfig:
    defaults: dict[str, object] = {
        "provider": Provider.openai,
        "model": "m",
        "max_concurrency": 1,
        "categories": [ReviewCategory.security],
        "reflect": False,
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)  # type: ignore[arg-type]


class TestPerLensFindingBound:
    """One lens must not be able to flood a review.

    Measured: a single lens returned 319 of a review's 323 findings on a diff
    with nothing wrong in it, every one at a distinct (path, line), so location
    dedupe collapsed none of them. The bound applies whatever the provider or
    token budget does.
    """

    def test_a_flooding_lens_is_bounded_with_a_notice(self) -> None:
        provider = _FloodingProvider(count=200)
        findings, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_findings_per_lens=25))
        assert len(findings) == 25
        assert "security" in summary and "175" in summary

    def test_an_ordinary_lens_is_untouched(self) -> None:
        """A normal review stays well under the bound and is unaffected."""
        provider = _FloodingProvider(count=3)
        findings, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_findings_per_lens=25))
        assert len(findings) == 3
        assert "dropped" not in summary

    def test_zero_disables_the_bound(self) -> None:
        """The escape hatch, spelled the way the rest of the config spells one
        (`max_review_seconds: 0`, `context_lines: 0`)."""
        provider = _FloodingProvider(count=60)
        findings, _ = LLMReviewEngine(provider).review(_CTX, _cfg(max_findings_per_lens=0))
        assert len(findings) == 60

    def test_the_bound_keeps_the_most_severe(self) -> None:
        """When the bound fires, the highest-severity findings are the ones kept."""
        payload = json.dumps(
            [
                {
                    "path": "a.py",
                    "line": 1,
                    "severity": "critical",
                    "title": "critical one",
                    "body": "b",
                    "failure_scenario": "When the changed line runs, data is lost.",
                },
                *[
                    {
                        "path": "a.py",
                        "line": i + 2,
                        "severity": "info",
                        "title": f"noise {i}",
                        "body": "b",
                        "failure_scenario": f"When line {i + 2} runs, nothing much happens.",
                    }
                    for i in range(40)
                ],
            ]
        )

        class _Mixed(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                return ProviderResult(text=payload, input_tokens=1, output_tokens=1)

        findings, _ = LLMReviewEngine(_Mixed()).review(_CTX, _cfg(max_findings_per_lens=1))
        assert [f.title for f in findings] == ["critical one"]
