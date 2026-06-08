"""Eval runner: review each fixture with a live model and report parse-rate + recall.

    python -m evals.run --provider ollama --model qwen3.6:35b \
        --api-base http://localhost:11434

Exits non-zero if any fixture failed to parse or fell below --min-recall, so it
can gate a model/setting change. Needs a live model, so it is NOT in the pytest
gate — run it on demand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lgtmaybe.core.models import PRContext, Provider, ReviewConfig
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
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


def _review(
    diff: str,
    manifest: Fixture,
    provider: Provider,
    model: str,
    api_base: str | None,
    *,
    timeout: int | None = None,
    num_ctx: int | None = None,
    max_input_tokens: int | None = None,
    reflect: bool = True,
):
    ctx = PRContext(
        diff=diff,
        changed_files=[manifest.changed_file],
        base_sha="0",
        head_sha="1",
        repo="eval/eval",
        pr_number=0,
    )
    cfg_overrides = {"max_input_tokens": max_input_tokens} if max_input_tokens is not None else {}
    cfg = ReviewConfig(
        provider=provider,
        model=model,
        api_base=api_base,
        timeout=timeout,
        reflect=reflect,
        **cfg_overrides,
    )
    # num_ctx is ollama's context window — litellm rejects it for hosted providers,
    # so only forward it on the ollama path.
    extra = {"num_ctx": num_ctx} if (num_ctx is not None and provider is Provider.ollama) else {}
    engine = LLMReviewEngine(
        build_provider(provider, model, api_base=api_base, timeout=timeout, **extra)
    )
    try:
        findings, _summary = engine.review(ctx, cfg)
        return score_fixture(manifest.name, findings, manifest.expected, parsed_ok=True)
    except ReviewIncompleteError:
        return score_fixture(manifest.name, [], manifest.expected, parsed_ok=False)


def _print(score: FixtureScore) -> None:
    status = "ok" if score.parsed_ok else "PARSE-FAIL"
    print(
        f"{score.name:14} parsed={status:10} "
        f"recall={score.recall:5.0%} ({score.matched_count}/{score.expected_count}) "
        f"findings={score.findings_count}"
    )
    for miss in score.missed:
        print(f"    missed: {miss}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run lgtmaybe review evals against a model.")
    ap.add_argument("--provider", required=True, choices=[p.value for p in Provider])
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--min-recall", type=float, default=0.6, help="fail below this recall")
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
    args = ap.parse_args(argv)

    provider = Provider(args.provider)
    scores = [
        _review(
            diff,
            m,
            provider,
            args.model,
            args.api_base,
            timeout=args.timeout,
            num_ctx=args.num_ctx,
            max_input_tokens=args.max_input_tokens,
            reflect=args.reflect,
        )
        for diff, m in _load_fixtures()
    ]
    for score in scores:
        _print(score)

    ok = all(s.parsed_ok and s.recall >= args.min_recall for s in scores)
    print("\n" + ("PASS" if ok else "FAIL") + f" (min recall {args.min_recall:.0%})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
