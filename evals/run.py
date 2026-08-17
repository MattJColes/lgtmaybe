"""Eval runner: review each fixture with a live model and report parse-rate + recall.

    python -m evals.run --provider ollama --model qwen3.6:35b \
        --api-base http://localhost:11434

Exits non-zero if any fixture failed to parse, or if recall *pooled across
fixtures* (total caught / total planted) fell below --min-recall — so it can gate
a model/setting change without flaking on a single-finding miss in one short
fixture. Needs a live model, so it is NOT in the pytest gate — run it on demand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

from lgtmaybe.core.models import Provider, ReviewCategory, ReviewConfig, StaticAnalysisConfig
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError, build_symbol_resolver
from lgtmaybe.engine.profiling import profiler
from lgtmaybe.local import local_file_reader
from lgtmaybe.providers.credentials import resolve_credentials
from lgtmaybe.providers.factory import build_provider

from .scorer import (
    Fixture,
    FixtureScore,
    _add_review_args,
    _eval_ctx,
    _sampling_extra,
    score_fixture,
)

# Fixtures default to this checkout's, but EVALS_FIXTURES_DIR overrides them so the
# A/B harness can point a baseline-ref worktree at the CURRENT tree's fixtures —
# keeping the yardstick fixed while only the reviewer code varies between legs.
_FIXTURES = Path(os.environ.get("EVALS_FIXTURES_DIR") or (Path(__file__).parent / "fixtures"))


def _load_fixtures() -> list[tuple[str, Fixture]]:
    out: list[tuple[str, Fixture]] = []
    for d in sorted(p for p in _FIXTURES.iterdir() if p.is_dir()):
        diff = (d / "diff.txt").read_text(encoding="utf-8")
        manifest = Fixture.model_validate_json((d / "expected.json").read_text(encoding="utf-8"))
        manifest.fixture_root = d
        # A `repo/` subdir is the fixture's on-disk corpus of unshown files — the
        # ones a cross-file deferral needs to verify. When present, the harness
        # roots a read-only reader + symbol resolver here (see `_review`).
        corpus = d / "repo"
        if corpus.is_dir():
            manifest.corpus_root = corpus
        # A `head/` subdir carries the changed files' HEAD text (what the
        # GitHub gateway would fetch), feeding static analysis and context
        # expansion during the eval run.
        head = d / "head"
        if head.is_dir():
            manifest.head_root = head
        out.append((diff, manifest))
    return out


def _select_fixtures(
    fixtures: list[tuple[str, Fixture]], names: list[str] | None
) -> list[tuple[str, Fixture]]:
    """Keep only the fixtures whose name is in *names* (all when *names* is empty).

    An unknown name is a hard error, not a silent skip: running zero fixtures would
    pool to 100% recall and pass vacuously, hiding a typo'd CI invocation.
    """
    if not names:
        return fixtures
    wanted = set(names)
    available = {m.name for _, m in fixtures}
    missing = wanted - available
    if missing:
        raise SystemExit(
            f"unknown fixture(s): {', '.join(sorted(missing))}. "
            f"Available: {', '.join(sorted(available))}"
        )
    return [(diff, m) for diff, m in fixtures if m.name in wanted]


def _parse_categories(value: str | None) -> list[ReviewCategory] | None:
    """Parse a comma-separated --categories value into review lenses (None = all).

    An unknown name is a hard error, not a silent skip: a typo'd lens would quietly
    run a different (or empty) fan-out and the recall bar would no longer mean what
    the CI invocation thinks it does.
    """
    if not value:
        return None
    valid = {c.value for c in ReviewCategory}
    names = [n.strip() for n in value.split(",") if n.strip()]
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise SystemExit(
            f"unknown categor(y/ies): {', '.join(unknown)}. Available: {', '.join(sorted(valid))}"
        )
    return [ReviewCategory(n) for n in names]


def _review(
    diff: str,
    manifest: Fixture,
    provider: Provider,
    model: str,
    api_base: str | None,
    *,
    api_key: str | None = None,
    timeout: int | None = None,
    num_ctx: int | None = None,
    max_input_tokens: int | None = None,
    reflect: bool = True,
    recursive: bool = True,
    symbol_resolution: bool = True,
    mid_review_retrieval: bool = False,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    categories: list[ReviewCategory] | None = None,
    context_lines: int | None = None,
    static_analysis: bool = False,
    triage_model: str | None = None,
    preset: str | None = None,
):
    ctx = _eval_ctx(diff, manifest)
    cfg_overrides: dict[str, object] = {}
    if max_input_tokens is not None:
        cfg_overrides["max_input_tokens"] = max_input_tokens
    if categories is not None:
        cfg_overrides["categories"] = categories
    if context_lines is not None:
        cfg_overrides["context_lines"] = context_lines
    if static_analysis:
        # A/B the F1 fusion: installed tools run over the fixture's head/ text
        # and their findings ground the lens prompts, exactly as in production.
        cfg_overrides["static_analysis"] = StaticAnalysisConfig(enabled=True)
    if triage_model is not None:
        cfg_overrides["triage_model"] = triage_model
    if mid_review_retrieval:
        # A/B the cross-file recall the one-round lens deferral buys: the same
        # fixtures, the same model, with and without the extra call.
        cfg_overrides["mid_review_retrieval"] = True
    if preset is not None:
        cfg_overrides["preset"] = preset
    cfg = ReviewConfig(
        provider=provider,
        model=model,
        api_base=api_base,
        timeout=timeout,
        reflect=reflect,
        recursive=recursive,
        # Evals measure model quality, never wall-clock discipline: the
        # production review deadline (max_review_seconds, default 1800s) would
        # skip calls mid-eval on a slow local model — a full-preset run on a
        # big fixture can exceed it serially — and silently melt the
        # recall it exists to measure. Always off here.
        max_review_seconds=0,
        **cfg_overrides,
    )
    # Sampling params + ollama's num_ctx reach the model via build_provider → litellm.
    extra = _sampling_extra(
        provider, num_ctx=num_ctx, temperature=temperature, top_p=top_p, top_k=top_k
    )
    # Resolve credentials the same way the CLI does, so the harness works against
    # a keyless openai-compatible endpoint (LM Studio / llama.cpp / vLLM) — which
    # needs the placeholder key the OpenAI client demands — and reads provider env
    # vars (OPENAI_API_KEY, OPENAI_COMPATIBLE_API_KEY, …) for hosted endpoints.
    auth = resolve_credentials(provider, api_key=api_key, api_base=api_base)
    # When the fixture ships an on-disk corpus of its unshown files, wire the same
    # read-only reader + ast-grep symbol resolver the CLI/Action use, rooted there.
    # A cross-file deferral can then fetch the real definition (a path directly, or
    # a symbol via ast-grep) and re-judge — the behaviour symbol resolution adds.
    # Toggle it off to A/B the forbidden-FP rate with vs without resolution.
    fetch_file = None
    resolve_symbol = None
    if manifest.corpus_root is not None:
        fetch_file = local_file_reader(manifest.corpus_root)
        if symbol_resolution:
            root = manifest.corpus_root
            resolve_symbol = build_symbol_resolver(lambda: root)
    engine = LLMReviewEngine(
        build_provider(
            provider,
            model,
            api_key=auth.api_key,
            api_base=auth.api_base,
            azure_ad_token=auth.azure_ad_token,
            timeout=timeout,
            **extra,
        ),
        fetch_file=fetch_file,
        resolve_symbol=resolve_symbol,
        # Root the workspace at the fixture's corpus, not the process cwd, so a
        # fixture that commits a spec (or directory-rule context files) is read
        # from ITS tree — and so a run started inside a repo that happens to have
        # its own `openspec/` never leaks that into a fixture's review.
        workspace_root=manifest.corpus_root or manifest.fixture_root,
    )
    # Per fixture, not per run: the profiler is a process-wide singleton, so
    # without this each fixture's totals carry every earlier fixture's calls —
    # and nothing here read them at all, so the drift was invisible.
    profiler.reset()
    try:
        findings, _summary = engine.review(ctx, cfg)
        parsed_ok = True
    except ReviewIncompleteError:
        findings, parsed_ok = [], False
    return score_fixture(
        manifest.name,
        findings,
        manifest.expected,
        forbidden=manifest.forbidden,
        parsed_ok=parsed_ok,
        # The per-call detail `parsed_ok` cannot carry. The engine already
        # records a reason per failed call — now shape-tagged — so a run can say
        # "3 of 4 lenses returned prose" instead of scoring a clean pass with
        # unexplained recall.
        lens_failures=[f"{c.label}: {c.error}" for c in profiler.calls if c.error],
        input_tokens=sum(c.input_tokens for c in profiler.calls),
        output_tokens=sum(c.output_tokens for c in profiler.calls),
    )


def _print(score: FixtureScore) -> None:
    status = "ok" if score.parsed_ok else "PARSE-FAIL"
    wrong = score.forbidden_count + score.unexpected_count
    right = score.adjudicable_count - wrong
    print(
        f"{score.name:14} parsed={status:10} "
        f"recall={score.recall:5.0%} ({score.matched_count}/{score.expected_count}) "
        f"precision={score.precision:5.0%} ({right}/{score.adjudicable_count}) "
        f"findings={score.findings_count} "
        f"anchored={score.anchored_rate:4.0%} ({score.anchored_count}/{score.findings_count})"
    )
    # Before the misses, because a failed call explains the misses under it: a
    # lens that never parsed did not "miss" anything, it never reported.
    for failure in score.lens_failures:
        print(f"    CALL FAILED: {failure}")
    for miss in score.missed:
        print(f"    missed: {miss}")
    for fp in score.false_positives:
        print(f"    FALSE POSITIVE: {fp}")


def _print_spread(scores: list[FixtureScore]) -> None:
    """Report the recall spread of every fixture run more than once.

    A mean on its own cannot say whether a model is reliable: 50% recall is a
    fixture half-caught every time, or fully caught half the time, and those are
    different defects. The min/max is what separates them — and `parsed=n/N` is
    the same distinction for the failure the structured-output audit turned on,
    where one model failed a case on every repeat and another failed it on some.
    """
    by_name: dict[str, list[FixtureScore]] = defaultdict(list)
    for score in scores:
        by_name[score.name].append(score)
    repeated = {name: runs for name, runs in by_name.items() if len(runs) > 1}
    if not repeated:
        return
    print()
    for name, runs in repeated.items():
        recalls = [s.recall for s in runs]
        parsed = sum(1 for s in runs if s.parsed_ok)
        print(
            f"{name:14} over {len(runs):2} runs  recall "
            f"mean={fmean(recalls):5.0%} min={min(recalls):5.0%} max={max(recalls):5.0%}  "
            f"parsed={parsed}/{len(runs)}"
        )


def _gate(scores: list[FixtureScore], min_recall: float) -> tuple[bool, float]:
    """Decide pass/fail for a run and report the aggregate recall.

    Two independent bars:
    - Every fixture must *parse* — an unparseable review is a real pipeline break
      (timeout, truncated context, refusal), not model variance, so any parse
      failure fails the run.
    - Recall is pooled across fixtures (total caught / total planted), not gated
      per-fixture. A small local model on CPU isn't bit-reproducible even at
      temperature 0, so a single missed finding on one short fixture shouldn't
      flip the whole job — pooling over more samples keeps the bar a real
      regression signal without flaking on that one-finding margin.
    - Every fixture must be *clean*: a forbidden (cross-file false-positive)
      finding firing is a humility regression, so any one fails the run. Only
      fixtures that declare `forbidden` can trip this — the rest are always clean.

    Under `--repeats N` the scores list carries N entries per fixture, so both
    all-quantified bars get strictly harsher: one parse failure or one forbidden
    hit in ANY repeat fails the run. That is deliberate for a false-positive
    gate — an intermittent humility regression is still a regression — but it
    does mean a repeated run is not comparable to a single-pass one at the same
    --min-recall. Recall pools over the repeats and stays comparable.
    """
    total_expected = sum(s.expected_count for s in scores)
    total_matched = sum(s.matched_count for s in scores)
    aggregate = total_matched / total_expected if total_expected else 1.0
    parsed = all(s.parsed_ok for s in scores)
    clean = all(s.clean for s in scores)
    return (parsed and clean and aggregate >= min_recall), aggregate


def pooled_metrics(scores: list[FixtureScore]) -> dict[str, float]:
    """Pool recall / precision / anchored across fixtures over raw counts.

    Each metric pools the underlying counts (total caught / total planted, etc.) —
    NOT an average of per-fixture percentages — so a fixture with more findings
    carries proportionally more weight. Shared by the JSON output and the A/B leg.
    """
    total_expected = sum(s.expected_count for s in scores)
    total_matched = sum(s.matched_count for s in scores)
    total_adjudicable = sum(s.adjudicable_count for s in scores)
    total_wrong = sum(s.forbidden_count + s.unexpected_count for s in scores)
    total_findings = sum(s.findings_count for s in scores)
    total_anchored = sum(s.anchored_count for s in scores)
    return {
        "pooled_recall": total_matched / total_expected if total_expected else 1.0,
        "pooled_precision": 1.0 - total_wrong / total_adjudicable if total_adjudicable else 1.0,
        "pooled_anchored": total_anchored / total_findings if total_findings else 1.0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run lgtmaybe review evals against a model.")
    _add_review_args(ap)
    ap.add_argument(
        "--api-key",
        default=None,
        help="API key for a hosted endpoint (else read from the provider's env var); "
        "omit for keyless local servers (ollama, LM Studio / llama.cpp / vLLM)",
    )
    ap.add_argument(
        "--min-recall",
        type=float,
        default=0.6,
        help="fail below this recall, pooled across fixtures (total caught / total planted)",
    )
    ap.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="ollama context window; raise so a large multi-file diff isn't truncated",
    )
    ap.add_argument(
        "--max-input-tokens",
        type=int,
        default=None,
        help="token budget per model call before the diff is split into batches",
    )
    ap.add_argument(
        "--context-lines",
        type=int,
        default=None,
        help="surrounding context lines padded around each hunk (ReviewConfig.context_lines); "
        "0 disables. Lets the A/B harness sweep the window width.",
    )
    ap.add_argument(
        "--no-reflect",
        dest="reflect",
        action="store_false",
        help="skip the self-reflection pass (weak local models over-prune their own findings)",
    )
    ap.add_argument(
        "--recursive",
        dest="recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="walk an over-budget file hunk-by-hunk (RLM, default on); --no-recursive "
        "pins the original whole-file method so a run can A/B the two strategies",
    )
    ap.add_argument(
        "--symbol-resolution",
        dest="symbol_resolution",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="for fixtures with an on-disk corpus, let ast-grep resolve a deferred "
        "symbol to its defining file (default on); --no-symbol-resolution pins the "
        "path-only fetch so a run can A/B the cross-file false-positive rate",
    )
    ap.add_argument(
        "--mid-review-retrieval",
        dest="mid_review_retrieval",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="let a review lens defer once for bounded read-only context from the "
        "fixture's repo/ corpus (default off) — run the same model with and without "
        "to measure what the extra call buys in cross-file recall and costs in tokens",
    )
    ap.add_argument(
        "--static-analysis",
        dest="static_analysis",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="enable static-analysis fusion (F1) so installed tools (ruff/bandit) "
        "ground the review with hints over the fixtures' head/ text — run the same "
        "model with and without to measure the fusion's recall delta",
    )
    ap.add_argument(
        "--triage-model",
        default=None,
        help="cheap triage model (P3) run before the strong --model, so a run can "
        "measure what two-stage routing costs in recall on the fixture set",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="run every selected fixture N times (default 1) — a single pass cannot "
        "tell a model that fails a case reproducibly from one that fails it "
        "intermittently, and that distinction is what a compliance audit turns on",
    )
    ap.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="emit the per-fixture scores + pooled metrics as a single JSON object on "
        "stdout (machine-readable; consumed by the evals.ab A/B harness)",
    )
    args = ap.parse_args(argv)

    provider = Provider(args.provider)
    categories = _parse_categories(args.categories)
    fixtures = _select_fixtures(_load_fixtures(), args.fixtures)
    scores = [
        _review(
            diff,
            m,
            provider,
            args.model,
            args.api_base,
            api_key=args.api_key,
            timeout=args.timeout,
            num_ctx=args.num_ctx,
            max_input_tokens=args.max_input_tokens,
            reflect=args.reflect,
            recursive=args.recursive,
            symbol_resolution=args.symbol_resolution,
            mid_review_retrieval=args.mid_review_retrieval,
            temperature=args.temperature,
            top_p=args.top_p,
            top_k=args.top_k,
            categories=categories,
            context_lines=args.context_lines,
            static_analysis=args.static_analysis,
            triage_model=args.triage_model,
            preset=args.preset,
        )
        # FLAT, deliberately: `evals.ab` parses `fixtures` as a plain list of
        # FixtureScore and pools raw counts, so N entries per fixture pool
        # correctly with no change there. Nesting would break it, and ab's
        # parsing has no test to catch that. Repeats are the INNER loop so a
        # fixture's runs print adjacent, under its own spread line.
        for diff, m in fixtures
        for _ in range(max(1, args.repeats))
    ]

    ok, aggregate = _gate(scores, args.min_recall)
    pooled = pooled_metrics(scores)

    if args.json_out:
        # Machine-readable: the per-fixture scores plus the pooled metrics, emitted
        # as the only thing on stdout so evals.ab can json.loads() it from a worktree.
        print(
            json.dumps(
                {
                    "fixtures": [s.model_dump() for s in scores],
                    "passed": ok,
                    # Named so a cross-model benchmark's own output says which
                    # model produced which row.
                    "provider": args.provider,
                    "model": args.model,
                    "repeats": args.repeats,
                    "aggregate_recall": aggregate,
                    **pooled,
                }
            )
        )
        return 0 if ok else 1

    for score in scores:
        _print(score)
    _print_spread(scores)
    print(
        f"\naggregate recall {aggregate:.0%} — "
        + ("PASS" if ok else "FAIL")
        + f" (min recall {args.min_recall:.0%})"
    )
    right = sum(s.adjudicable_count - s.forbidden_count - s.unexpected_count for s in scores)
    adjudicable = sum(s.adjudicable_count for s in scores)
    print(
        f"pooled precision {pooled['pooled_precision']:.0%} "
        f"({right}/{adjudicable} adjudicable findings right)"
    )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
