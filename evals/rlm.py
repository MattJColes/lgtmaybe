"""RLM A/B benchmark — does the recursive hunk-walk beat reviewing a file whole?

This measures the shipped ``ReviewConfig.recursive`` feature (engine default on):
when a file's diff exceeds ``max_input_tokens`` the engine can either review the
whole file in one call or walk it hunk-by-hunk (each hunk its own focused call).
Two effects are in play — a *focus* gain (a small model attends better to one
hunk at a time) on any over-budget file, and *truncation-avoidance* when the file
genuinely exceeds the model's context window, where the whole-file call drops the
tail while the walk reviews every hunk. This harness runs both on a fixture
against a **live** model and reports recall + token usage (a cost proxy) for each,
so the "it helps performance" claim can be checked on real numbers, not a hunch:

  - ``whole``     — ``recursive=False``: the over-budget file in one call.
  - ``recursive`` — ``recursive=True``:  the RLM walk, each hunk its own call.

Both strategies run through the **real** ``LLMReviewEngine`` (same redaction,
injection wrapping, fan-out, dedupe) — they differ only by the one config flag,
so the comparison is honest. It needs a live model, so it is **not** in the
pytest gate; the pure plumbing (usage accounting, the comparison record) is
unit-tested in ``tests/evals/test_rlm.py``.

    python -m evals.rlm --provider ollama --model qwen3.5:4b \
        --api-base http://localhost:11434 --fixture rlm-bigfile --budget 300
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from pydantic import BaseModel

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
)
from lgtmaybe.core.ports import Message, ProviderClient
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
from lgtmaybe.providers.factory import build_provider

from .run import _load_fixtures, _select_fixtures
from .scorer import Fixture, FixtureScore, score_fixture

# ---------------------------------------------------------------------------
# Pure plumbing — no model, no I/O; unit-tested.
# ---------------------------------------------------------------------------


class _UsageTrackingProvider(ProviderClient):
    """Wraps a real provider and accumulates token usage + call count.

    Lets the benchmark compare the two strategies on the same cost proxy (tokens),
    fairly — both run through the same wrapped backend.
    """

    def __init__(self, inner: ProviderClient) -> None:
        self._inner = inner
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        result = self._inner.complete(messages, model, **opts)
        self.calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        return result


class StrategyResult(BaseModel):
    """One strategy's outcome on a fixture: quality (score) + cost (tokens)."""

    name: str
    score: FixtureScore
    input_tokens: int
    output_tokens: int
    calls: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ComparisonResult(BaseModel):
    """Side-by-side of the whole-file baseline and the recursive walk."""

    whole: StrategyResult
    recursive: StrategyResult

    @property
    def recall_delta(self) -> float:
        """Recursive recall minus whole recall (positive = recursive caught more)."""
        return self.recursive.score.recall - self.whole.score.recall

    @property
    def token_ratio(self) -> float:
        """Recursive total tokens / whole total tokens (>1 = recursive costs more)."""
        if self.whole.total_tokens == 0:
            return 1.0
        return self.recursive.total_tokens / self.whole.total_tokens

    @property
    def verdict(self) -> str:
        """A one-line read: did walking the diff pay for itself here?"""
        better = self.recall_delta > 0
        cheaper = self.token_ratio < 1.0
        if better and cheaper:
            return "recursive wins — higher recall AND cheaper"
        if better:
            return f"recursive recall +{self.recall_delta:.0%} at {self.token_ratio:.1f}x tokens"
        if cheaper:
            return f"recursive cheaper ({self.token_ratio:.1f}x tokens), recall not worse"
        return "whole-file holds — recursive cost more without catching more"


# ---------------------------------------------------------------------------
# Live runners — need a model.
# ---------------------------------------------------------------------------


def _ctx(diff: str, manifest: Fixture) -> PRContext:
    return PRContext(
        diff=diff,
        changed_files=[manifest.changed_file],
        base_sha="0",
        head_sha="1",
        repo="eval/eval",
        pr_number=0,
    )


def _run(
    diff: str,
    manifest: Fixture,
    provider: _UsageTrackingProvider,
    model: str,
    *,
    name: str,
    recursive: bool,
    budget: int,
    reflect: bool,
) -> StrategyResult:
    """Review *diff* through the real engine with ``recursive`` on or off."""
    cfg = ReviewConfig(
        provider=Provider.ollama,  # provider value is unused once a client is injected
        model=model,
        max_input_tokens=budget,
        recursive=recursive,
        reflect=reflect,
    )
    engine = LLMReviewEngine(provider)
    try:
        findings, _ = engine.review(_ctx(diff, manifest), cfg)
        score = score_fixture(manifest.name, findings, manifest.expected, parsed_ok=True)
    except ReviewIncompleteError:
        score = score_fixture(manifest.name, [], manifest.expected, parsed_ok=False)
    return StrategyResult(
        name=name,
        score=score,
        input_tokens=provider.input_tokens,
        output_tokens=provider.output_tokens,
        calls=provider.calls,
    )


def _print(result: StrategyResult) -> None:
    status = "ok" if result.score.parsed_ok else "PARSE-FAIL"
    print(
        f"{result.name:10} parsed={status:10} "
        f"recall={result.score.recall:5.0%} "
        f"({result.score.matched_count}/{result.score.expected_count}) "
        f"calls={result.calls:3} tokens={result.total_tokens:6} "
        f"(in {result.input_tokens} / out {result.output_tokens})"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--provider", required=True, choices=[p.value for p in Provider])
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--timeout", type=int, default=None)
    ap.add_argument(
        "--budget",
        type=int,
        default=300,
        help="max_input_tokens per call — set it below a file's diff size (but above "
        "a single hunk) so the over-budget file splits and the comparison is meaningful",
    )
    ap.add_argument("--no-reflect", dest="reflect", action="store_false")
    ap.add_argument(
        "--fixture",
        action="append",
        dest="fixtures",
        metavar="NAME",
        help="fixture(s) to compare on; repeatable. Default: all.",
    )
    args = ap.parse_args(argv)

    provider = Provider(args.provider)
    fixtures = _select_fixtures(_load_fixtures(), args.fixtures)

    exit_code = 0
    for diff, manifest in fixtures:
        print(f"\n=== {manifest.name} (budget {args.budget} tokens/call) ===")
        whole_tracker = _UsageTrackingProvider(
            build_provider(provider, args.model, api_base=args.api_base, timeout=args.timeout)
        )
        rec_tracker = _UsageTrackingProvider(
            build_provider(provider, args.model, api_base=args.api_base, timeout=args.timeout)
        )
        comparison = ComparisonResult(
            whole=_run(
                diff,
                manifest,
                whole_tracker,
                args.model,
                name="whole",
                recursive=False,
                budget=args.budget,
                reflect=args.reflect,
            ),
            recursive=_run(
                diff,
                manifest,
                rec_tracker,
                args.model,
                name="recursive",
                recursive=True,
                budget=args.budget,
                reflect=args.reflect,
            ),
        )
        _print(comparison.whole)
        _print(comparison.recursive)
        print(f"  → {comparison.verdict}")
        if not comparison.recursive.score.parsed_ok:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
