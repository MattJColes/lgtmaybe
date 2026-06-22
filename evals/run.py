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
import sys
from pathlib import Path

from lgtmaybe.core.models import PRContext, Provider, ReviewCategory, ReviewConfig
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
from lgtmaybe.providers.credentials import resolve_credentials
from lgtmaybe.providers.factory import build_provider

from .scorer import Fixture, FixtureScore, score_fixture

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixtures() -> list[tuple[str, Fixture]]:
    out: list[tuple[str, Fixture]] = []
    for d in sorted(p for p in _FIXTURES.iterdir() if p.is_dir()):
        diff = (d / "diff.txt").read_text()
        manifest = Fixture.model_validate_json((d / "expected.json").read_text())
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
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    categories: list[ReviewCategory] | None = None,
):
    ctx = PRContext(
        diff=diff,
        changed_files=[manifest.changed_file],
        base_sha="0",
        head_sha="1",
        repo="eval/eval",
        pr_number=0,
    )
    cfg_overrides: dict[str, object] = {}
    if max_input_tokens is not None:
        cfg_overrides["max_input_tokens"] = max_input_tokens
    if categories is not None:
        cfg_overrides["categories"] = categories
    cfg = ReviewConfig(
        provider=provider,
        model=model,
        api_base=api_base,
        timeout=timeout,
        reflect=reflect,
        recursive=recursive,
        **cfg_overrides,
    )
    # num_ctx is ollama's context window — litellm rejects it for hosted providers,
    # so only forward it on the ollama path.
    extra: dict[str, object] = (
        {"num_ctx": num_ctx} if (num_ctx is not None and provider is Provider.ollama) else {}
    )
    # Sampling params reach the model via the provider's default_opts → litellm.
    # Only forward the ones explicitly given so an unset flag keeps the model's own
    # default rather than pinning it to something. litellm.drop_params handles a
    # param a given provider can't take (e.g. top_k on an OpenAI-compat endpoint).
    if temperature is not None:
        extra["temperature"] = temperature
    if top_p is not None:
        extra["top_p"] = top_p
    if top_k is not None:
        extra["top_k"] = top_k
    # Resolve credentials the same way the CLI does, so the harness works against
    # a keyless openai-compatible endpoint (LM Studio / llama.cpp / vLLM) — which
    # needs the placeholder key the OpenAI client demands — and reads provider env
    # vars (OPENAI_API_KEY, OPENAI_COMPATIBLE_API_KEY, …) for hosted endpoints.
    auth = resolve_credentials(provider, api_key=api_key, api_base=api_base)
    engine = LLMReviewEngine(
        build_provider(
            provider,
            model,
            api_key=auth.api_key,
            api_base=auth.api_base,
            azure_ad_token=auth.azure_ad_token,
            timeout=timeout,
            **extra,
        )
    )
    try:
        findings, _summary = engine.review(ctx, cfg)
        return score_fixture(
            manifest.name,
            findings,
            manifest.expected,
            forbidden=manifest.forbidden,
            parsed_ok=True,
        )
    except ReviewIncompleteError:
        return score_fixture(
            manifest.name, [], manifest.expected, forbidden=manifest.forbidden, parsed_ok=False
        )


def _print(score: FixtureScore) -> None:
    status = "ok" if score.parsed_ok else "PARSE-FAIL"
    print(
        f"{score.name:14} parsed={status:10} "
        f"recall={score.recall:5.0%} ({score.matched_count}/{score.expected_count}) "
        f"findings={score.findings_count} "
        f"anchored={score.anchored_rate:4.0%} ({score.anchored_count}/{score.findings_count})"
    )
    for miss in score.missed:
        print(f"    missed: {miss}")
    for fp in score.false_positives:
        print(f"    FALSE POSITIVE: {fp}")


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
    """
    total_expected = sum(s.expected_count for s in scores)
    total_matched = sum(s.matched_count for s in scores)
    aggregate = total_matched / total_expected if total_expected else 1.0
    parsed = all(s.parsed_ok for s in scores)
    clean = all(s.clean for s in scores)
    return (parsed and clean and aggregate >= min_recall), aggregate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run lgtmaybe review evals against a model.")
    ap.add_argument("--provider", required=True, choices=[p.value for p in Provider])
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-base", default=None)
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
        "--timeout",
        type=int,
        default=None,
        help="per-request timeout (seconds); raise for slow local models on big diffs",
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
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature forwarded to the model (default: the model's own)",
    )
    ap.add_argument(
        "--top-p",
        type=float,
        default=None,
        help="nucleus-sampling top_p forwarded to the model",
    )
    ap.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="top_k forwarded to the model (ollama/qwen3.x recommend 20 with thinking off)",
    )
    ap.add_argument(
        "--categories",
        default=None,
        help="comma-separated review lenses to run (default: all). Cuts the per-category "
        "fan-out for a fast CI smoke, e.g. 'security,correctness'.",
    )
    ap.add_argument(
        "--fixture",
        action="append",
        dest="fixtures",
        metavar="NAME",
        help="only run the named fixture(s); repeatable. Default: all. Lets CI run a fast "
        "single-fixture subset while the full set stays available on demand.",
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
            temperature=args.temperature,
            top_p=args.top_p,
            top_k=args.top_k,
            categories=categories,
        )
        for diff, m in fixtures
    ]
    for score in scores:
        _print(score)

    ok, aggregate = _gate(scores, args.min_recall)
    print(
        f"\naggregate recall {aggregate:.0%} — "
        + ("PASS" if ok else "FAIL")
        + f" (min recall {args.min_recall:.0%})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
