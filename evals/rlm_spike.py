"""RLM-style recursive hunk-walking review — an on-demand research spike.

Roadmap question (docs/explanation/foss-and-the-future.md): can a cheap model
*walk* a large diff hunk-by-hunk and beat today's "truncate to a token budget"
behaviour on cost and recall? When a diff exceeds ``max_input_tokens`` the engine
drops the tail; an RLM-style loop instead reviews each hunk in its own small call,
so nothing is dropped and every sub-call's context stays small (where token cost
and small-model accuracy both live).

This compares two strategies on a fixture against a **live** model:

  - ``truncated`` — the normal engine with a tight ``max_input_tokens``: today's
    behaviour on an over-budget diff (the tail is dropped).
  - ``recursive`` — split the diff into hunks and review each in its own call,
    then merge + dedupe the findings.

It reports parse-rate, recall, and token usage (a cost proxy) for each, so a
change can be judged on real numbers rather than a hunch. It needs a live model,
so it is **not** in the pytest gate — the pure plumbing (hunk splitting, usage
accounting, the comparison record) is unit-tested in
``tests/evals/test_rlm_spike.py``.

    python -m evals.rlm_spike --provider ollama --model qwen3.6:27b \
        --api-base http://localhost:11434 --fixture vibe-multifile --budget 1500

The same hardening as a real review still applies (the recursive path reuses the
engine per hunk, so redaction + injection wrapping run on every sub-call).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from pydantic import BaseModel

from lgtmaybe.core.diffparse import split_by_file
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
    ReviewFinding,
)
from lgtmaybe.core.ports import Message, ProviderClient
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
from lgtmaybe.providers.factory import build_provider

from .run import _load_fixtures, _select_fixtures
from .scorer import Fixture, FixtureScore, score_fixture

# ---------------------------------------------------------------------------
# Pure plumbing — no model, no I/O; unit-tested.
# ---------------------------------------------------------------------------


def split_into_hunks(diff: str, changed_files: list[str]) -> list[str]:
    """Split a unified diff into standalone single-hunk mini-diffs.

    Each returned string carries its file header (the ``diff --git`` / ``---`` /
    ``+++`` lines) followed by exactly one ``@@`` hunk, so it is a valid diff that
    can be reviewed on its own — the unit an RLM-style walk recurses over. A file
    patch with no ``@@`` hunk (pure rename/mode change) is returned whole.
    """
    units: list[str] = []
    for _path, patch in split_by_file(diff, changed_files):
        lines = patch.splitlines(keepends=True)
        first_hunk = next((i for i, ln in enumerate(lines) if ln.startswith("@@")), None)
        if first_hunk is None:
            units.append(patch)
            continue
        header = lines[:first_hunk]
        current: list[str] = []
        for line in lines[first_hunk:]:
            if line.startswith("@@") and current:
                units.append("".join(header + current))
                current = []
            current.append(line)
        if current:
            units.append("".join(header + current))
    return units


class _UsageTrackingProvider(ProviderClient):
    """Wraps a real provider and accumulates token usage + call count.

    Lets the spike compare the two strategies on the same cost proxy (tokens),
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
    """Side-by-side of the truncated baseline and the recursive strategy."""

    truncated: StrategyResult
    recursive: StrategyResult

    @property
    def recall_delta(self) -> float:
        """Recursive recall minus truncated recall (positive = recursive caught more)."""
        return self.recursive.score.recall - self.truncated.score.recall

    @property
    def token_ratio(self) -> float:
        """Recursive total tokens / truncated total tokens (>1 = recursive costs more)."""
        if self.truncated.total_tokens == 0:
            return 1.0
        return self.recursive.total_tokens / self.truncated.total_tokens

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
        return "truncated holds — recursive cost more without catching more"


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


def run_truncated(
    diff: str,
    manifest: Fixture,
    provider: _UsageTrackingProvider,
    model: str,
    *,
    budget: int,
    reflect: bool,
) -> StrategyResult:
    """Baseline: one review with a tight token budget — the over-budget tail drops."""
    cfg = ReviewConfig(
        provider=Provider.ollama,  # provider value is unused once a client is injected
        model=model,
        max_input_tokens=budget,
        reflect=reflect,
    )
    engine = LLMReviewEngine(provider)
    try:
        findings, _ = engine.review(_ctx(diff, manifest), cfg)
        score = score_fixture(manifest.name, findings, manifest.expected, parsed_ok=True)
    except ReviewIncompleteError:
        score = score_fixture(manifest.name, [], manifest.expected, parsed_ok=False)
    return StrategyResult(
        name="truncated",
        score=score,
        input_tokens=provider.input_tokens,
        output_tokens=provider.output_tokens,
        calls=provider.calls,
    )


def run_recursive(
    diff: str,
    manifest: Fixture,
    provider: _UsageTrackingProvider,
    model: str,
    *,
    budget: int,
    reflect: bool,
) -> StrategyResult:
    """RLM-style: review each hunk in its own small call, then merge the findings.

    Nothing is dropped — the model sees every hunk — and each call's context stays
    small. Reuses the full engine per hunk so redaction + injection still run.
    """
    engine = LLMReviewEngine(provider)
    merged: dict[tuple[str, int, str, str], ReviewFinding] = {}
    any_parsed = False
    for hunk in split_into_hunks(diff, [manifest.changed_file]):
        cfg = ReviewConfig(
            provider=Provider.ollama,
            model=model,
            max_input_tokens=budget,
            reflect=reflect,
        )
        try:
            findings, _ = engine.review(_ctx(hunk, manifest), cfg)
            any_parsed = True
        except ReviewIncompleteError:
            continue
        for f in findings:
            merged[(f.path, f.line, f.side, f.title.strip().lower())] = f
    score = score_fixture(
        manifest.name, list(merged.values()), manifest.expected, parsed_ok=any_parsed
    )
    return StrategyResult(
        name="recursive",
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
        default=1500,
        help="max_input_tokens per call — set it below the fixture diff size so the "
        "truncated baseline actually drops its tail and the comparison is meaningful",
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
        trunc_tracker = _UsageTrackingProvider(
            build_provider(provider, args.model, api_base=args.api_base, timeout=args.timeout)
        )
        rec_tracker = _UsageTrackingProvider(
            build_provider(provider, args.model, api_base=args.api_base, timeout=args.timeout)
        )
        comparison = ComparisonResult(
            truncated=run_truncated(
                diff, manifest, trunc_tracker, args.model, budget=args.budget, reflect=args.reflect
            ),
            recursive=run_recursive(
                diff, manifest, rec_tracker, args.model, budget=args.budget, reflect=args.reflect
            ),
        )
        _print(comparison.truncated)
        _print(comparison.recursive)
        print(f"  → {comparison.verdict}")
        if not comparison.recursive.score.parsed_ok:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
