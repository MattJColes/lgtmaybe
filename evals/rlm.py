"""RLM A/B benchmark — run via ``python -m evals.rlm`` against a local model.

Compares the **recursive hunk-walk** against the original **whole-file** method
through the real ``LLMReviewEngine`` — they differ only by ``ReviewConfig.recursive``
— over one or more fixtures, repeated ``--repeats`` times so the spread is visible
(the model samples at temperature > 0, so a single run is noisy). Reports each
strategy's recall mean / min / max plus mean token cost, and a verdict.

This is the common way to check the RLM walk locally with ollama:

    python -m evals.rlm --provider ollama --model qwen3.5:4b \
        --api-base http://localhost:11434 --repeats 8

It needs a live model, so it is **not** in the pytest gate; the pure aggregation
(stats, the per-strategy report, the verdict) is unit-tested in
``tests/evals/test_rlm.py``. The same hardening as a real review still applies —
both strategies run the full engine, so redaction + injection wrapping run on
every call.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from typing import Any

from pydantic import BaseModel

from lgtmaybe.core.models import Provider, ProviderResult, ReviewConfig
from lgtmaybe.core.ports import Message, ProviderClient
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
from lgtmaybe.providers.factory import build_provider

from .run import _load_fixtures, _parse_categories, _select_fixtures
from .scorer import Fixture, _add_review_args, _eval_ctx, _sampling_extra, score_fixture

# The fixtures purpose-built for this benchmark: each is a single multi-hunk file
# big enough that a low --budget forces the over-budget split.
_DEFAULT_FIXTURES = ["rlm-bigfile", "rlm-pipeline"]


# ---------------------------------------------------------------------------
# Pure plumbing — no model, no I/O; unit-tested.
# ---------------------------------------------------------------------------


class _UsageTrackingProvider(ProviderClient):
    """Wraps a real provider and accumulates token usage + call count per run."""

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


class RunSample(BaseModel):
    """One run of one strategy over all fixtures: pooled recall + that run's cost."""

    recall: float
    input_tokens: int
    output_tokens: int
    calls: int
    parsed_ok: bool

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class StrategyReport(BaseModel):
    """A strategy's outcome aggregated across repeats: recall spread + mean cost."""

    name: str
    samples: list[RunSample]

    @property
    def recalls(self) -> list[float]:
        return [s.recall for s in self.samples]

    @property
    def mean_recall(self) -> float:
        return statistics.fmean(self.recalls) if self.samples else 0.0

    @property
    def mean_total_tokens(self) -> float:
        return statistics.fmean([s.total_tokens for s in self.samples]) if self.samples else 0.0

    @property
    def all_parsed(self) -> bool:
        return all(s.parsed_ok for s in self.samples)


def verdict(whole: StrategyReport, recursive: StrategyReport) -> str:
    """A one-line read comparing mean recall and mean token cost of the two."""
    delta = recursive.mean_recall - whole.mean_recall  # fraction
    ratio = (
        recursive.mean_total_tokens / whole.mean_total_tokens if whole.mean_total_tokens else 1.0
    )
    better = delta > 0
    cheaper = ratio < 1.0
    if better and cheaper:
        return "recursive wins — higher mean recall AND cheaper"
    if better:
        return f"recursive recall +{delta:.0%} (mean) at {ratio:.1f}x tokens"
    if cheaper:
        return f"recursive cheaper ({ratio:.1f}x tokens), mean recall not worse"
    return "whole-file holds — recursive cost more without catching more"


# ---------------------------------------------------------------------------
# Live runner — needs a model.
# ---------------------------------------------------------------------------


def _run_strategy_once(
    fixtures: list[tuple[str, Fixture]],
    make_provider: Any,
    provider: Provider,
    model: str,
    *,
    recursive: bool,
    budget: int | None,
    reflect: bool,
    categories: Any,
) -> RunSample:
    """Review every fixture once under one strategy; pool recall across them."""
    tracker = _UsageTrackingProvider(make_provider())
    engine = LLMReviewEngine(tracker)
    matched = expected = 0
    parsed_all = True
    overrides: dict[str, Any] = {"recursive": recursive, "reflect": reflect}
    if budget is not None:
        overrides["max_input_tokens"] = budget
    if categories is not None:
        overrides["categories"] = categories
    for diff, manifest in fixtures:
        cfg = ReviewConfig(provider=provider, model=model, **overrides)
        try:
            findings, _ = engine.review(_eval_ctx(diff, manifest), cfg)
            score = score_fixture(manifest.name, findings, manifest.expected, parsed_ok=True)
        except ReviewIncompleteError:
            score = score_fixture(manifest.name, [], manifest.expected, parsed_ok=False)
            parsed_all = False
        matched += score.matched_count
        expected += score.expected_count
    recall = matched / expected if expected else 1.0
    return RunSample(
        recall=recall,
        input_tokens=tracker.input_tokens,
        output_tokens=tracker.output_tokens,
        calls=tracker.calls,
        parsed_ok=parsed_all,
    )


def _print(report: StrategyReport) -> None:
    parsed = "ok" if report.all_parsed else "PARSE-FAIL"
    recalls = report.recalls
    lo = min(recalls) if recalls else 0.0
    hi = max(recalls) if recalls else 0.0
    print(
        f"{report.name:10} parsed={parsed:10} "
        f"recall mean {report.mean_recall:4.0%} "
        f"(min {lo:.0%}, max {hi:.0%}, spread {hi - lo:.0%}) "
        f"tokens ~{report.mean_total_tokens:.0f}  over {len(report.samples)} runs"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _add_review_args(ap)
    ap.add_argument("--num-ctx", type=int, default=None, help="ollama context window")
    ap.add_argument(
        "--budget",
        type=int,
        default=300,
        help="--max-input-tokens per call — set below a fixture's diff size (but above "
        "a single hunk) so the over-budget file splits and the strategies diverge",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="runs per strategy; raise it for a wide, low-noise sweep (the model "
        "samples at temperature > 0, so one run is noisy)",
    )
    ap.add_argument(
        "--only",
        choices=["whole", "recursive", "both"],
        default="both",
        help="run just one strategy (lets a CI matrix parallelise the two legs)",
    )
    ap.add_argument("--no-reflect", dest="reflect", action="store_false")
    args = ap.parse_args(argv)

    provider = Provider(args.provider)
    categories = _parse_categories(args.categories)
    fixtures = _select_fixtures(_load_fixtures(), args.fixtures or _DEFAULT_FIXTURES)

    # Sampling + ollama context reach the model via build_provider → litellm.
    extra = _sampling_extra(
        provider,
        num_ctx=args.num_ctx,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
    )

    def make_provider() -> ProviderClient:
        return build_provider(
            provider, args.model, api_base=args.api_base, timeout=args.timeout, **extra
        )

    names = ", ".join(m.name for _, m in fixtures)
    print(f"\n=== RLM benchmark (model {args.model}, {args.repeats}x, fixtures: {names}) ===")

    reports: dict[str, StrategyReport] = {}
    for name, recursive in (("whole", False), ("recursive", True)):
        if args.only != "both" and args.only != name:
            continue
        samples = [
            _run_strategy_once(
                fixtures,
                make_provider,
                provider,
                args.model,
                recursive=recursive,
                budget=args.budget,
                reflect=args.reflect,
                categories=categories,
            )
            for _ in range(args.repeats)
        ]
        reports[name] = StrategyReport(name=name, samples=samples)
        _print(reports[name])

    if "whole" in reports and "recursive" in reports:
        print(f"  → {verdict(reports['whole'], reports['recursive'])}")

    # Non-zero only on a real pipeline break (a strategy that never parsed), so the
    # benchmark reports numbers rather than gating on model recall.
    return 0 if all(r.all_parsed for r in reports.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
