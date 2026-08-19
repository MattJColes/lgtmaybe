"""A/B benchmark — compare the reviewer at a baseline git ref against the working tree.

    python -m evals.ab --baseline-ref main --provider ollama --model qwen3:4b \
        --api-base http://localhost:11434

Runs the same fixtures twice — once with the reviewer *code* checked out at
``--baseline-ref`` (in a throwaway ``git worktree``), once against the current
working tree — and reports the pooled recall / precision deltas so a prompt or
pipeline change can be measured rather than eyeballed. Temperature is pinned to 0
in both legs and the **fixtures are always read from the current tree**, so the
only thing that varies is the reviewer code (or, with ``--sweep``, one config
axis): the fixtures are the fixed yardstick.

A config axis (``--sweep context-lines=20,40,0``) sweeps one ``evals.run``
option across the *current* tree instead of comparing two refs, so a
configuration question is answerable on one checkout.

This needs a live model, so only the pure aggregation (pooling,
deltas, verdict) is in the pytest gate (``tests/evals/test_ab.py``). It exits
non-zero only on a *pipeline* break (a leg that wouldn't run / parse), never on
the deltas — it reports numbers, it doesn't gate them.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel

from .run import pooled_metrics
from .scorer import FixtureScore, _add_review_args

# ---------------------------------------------------------------------------
# Pure aggregation — no model, no git, no I/O; unit-tested.
# ---------------------------------------------------------------------------


class ABLeg(BaseModel):
    """One side of the comparison: a ref's pooled metrics + its per-fixture scores."""

    ref: str
    pooled_recall: float
    pooled_precision: float
    anchored_rate: float
    per_fixture: list[FixtureScore]


class ABReport(BaseModel):
    """Two legs and the deltas between them (current − baseline)."""

    baseline: ABLeg
    current: ABLeg

    @property
    def recall_delta(self) -> float:
        return self.current.pooled_recall - self.baseline.pooled_recall

    @property
    def precision_delta(self) -> float:
        return self.current.pooled_precision - self.baseline.pooled_precision


def _pool_legs(ref: str, scores: list[FixtureScore]) -> ABLeg:
    """Build an ABLeg by pooling *scores* over raw counts (not averaged percentages)."""
    pooled = pooled_metrics(scores)
    return ABLeg(
        ref=ref,
        pooled_recall=pooled["pooled_recall"],
        pooled_precision=pooled["pooled_precision"],
        anchored_rate=pooled["pooled_anchored"],
        per_fixture=scores,
    )


def ab_verdict(report: ABReport) -> str:
    """A one-line read of the recall/precision deltas between the two legs."""
    rd = report.recall_delta
    pd = report.precision_delta
    if abs(rd) < 0.005 and abs(pd) < 0.005:
        return "no change — recall and precision are flat vs the baseline"
    return f"recall {rd:+.0%}, precision {pd:+.0%} vs {report.baseline.ref}"


# ---------------------------------------------------------------------------
# Live runner — needs a model + git.
# ---------------------------------------------------------------------------


def _run_json(
    cwd: Path,
    fixtures_dir: Path,
    *,
    provider: str,
    model: str,
    extra_args: list[str],
) -> list[FixtureScore]:
    """Run ``python -m evals.run --json`` in *cwd*, reading fixtures from *fixtures_dir*.

    Temperature is pinned to 0 and fixtures come from the *current* tree (passed via
    ``EVALS_FIXTURES_DIR``) so only the reviewer code under *cwd* varies between legs.
    Returns the parsed per-fixture FixtureScore list. Raises on a non-JSON / failed run
    (a pipeline break).
    """
    env = dict(os.environ)
    env["EVALS_FIXTURES_DIR"] = str(fixtures_dir)
    cmd = [
        sys.executable,
        "-m",
        "evals.run",
        "--provider",
        provider,
        "--model",
        model,
        "--min-recall",
        "0.0",
        "--temperature",
        "0",
        "--json",
        *extra_args,
    ]
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"eval leg in {cwd} broke:\n{proc.stderr}")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return [FixtureScore.model_validate(f) for f in payload["fixtures"]]


def _baseline_leg(
    ref: str,
    fixtures_dir: Path,
    *,
    provider: str,
    model: str,
    extra_args: list[str],
) -> ABLeg:
    """Check the reviewer code out at *ref* in a throwaway worktree and run a leg there."""
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="lgtmaybe-ab-") as tmp:
        worktree = Path(tmp) / "wt"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), ref],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            scores = _run_json(
                worktree,
                fixtures_dir,
                provider=provider,
                model=model,
                extra_args=extra_args,
            )
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
    return _pool_legs(ref, scores)


def _current_leg(
    fixtures_dir: Path,
    *,
    provider: str,
    model: str,
    extra_args: list[str],
    label: str = "working-tree",
) -> ABLeg:
    repo_root = Path(__file__).resolve().parents[1]
    scores = _run_json(
        repo_root, fixtures_dir, provider=provider, model=model, extra_args=extra_args
    )
    return _pool_legs(label, scores)


def _print_report(report: ABReport) -> None:
    for leg in (report.baseline, report.current):
        print(
            f"{leg.ref:16} recall {leg.pooled_recall:5.0%}  "
            f"precision {leg.pooled_precision:5.0%}  anchored {leg.anchored_rate:5.0%}"
        )
    print(f"  → {ab_verdict(report)}")


def _provider_passthrough(args: argparse.Namespace) -> list[str]:
    """Build the evals.run flags that aren't ref/config-axis specific."""
    out: list[str] = []
    if args.api_base:
        out += ["--api-base", args.api_base]
    if args.api_key:
        out += ["--api-key", args.api_key]
    if args.timeout is not None:
        out += ["--timeout", str(args.timeout)]
    if args.max_input_tokens is not None:
        out += ["--max-input-tokens", str(args.max_input_tokens)]
    if args.categories:
        out += ["--categories", args.categories]
    for name in args.fixtures or []:
        out += ["--fixture", name]
    return out


def _parse_sweep(value: str) -> tuple[str, list[str]]:
    field, separator, raw_values = value.partition("=")
    field = field.strip().replace("_", "-")
    values = [item.strip() for item in raw_values.split(",") if item.strip()]
    if not separator or not field or len(values) < 2:
        raise ValueError("--sweep must be NAME=VALUE,VALUE with at least two values")
    if not field.replace("-", "").isalnum():
        raise ValueError("--sweep field must contain only letters, numbers, '-' or '_'")
    return field, values


def _sweep_args(field: str, value: str) -> list[str]:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return [f"--{field}" if lowered == "true" else f"--no-{field}"]
    return [f"--{field}", value]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Provider, model, api-base, timeout, categories, preset and fixture are the
    # same flags evals.run takes, so they are declared once, there. Sampling is
    # deliberately not among them: both legs are pinned to temperature 0.
    _add_review_args(ap)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--max-input-tokens", type=int, default=None)
    ap.add_argument(
        "--baseline-ref",
        default=None,
        help="git ref to check the reviewer code out at for the baseline leg "
        "(e.g. main, a tag, a sha). Compared against the current working tree.",
    )
    ap.add_argument(
        "--sweep",
        default=None,
        metavar="NAME=VALUE,VALUE",
        help="sweep one evals.run option on the current tree; first value is baseline",
    )
    args = ap.parse_args(argv)

    fixtures_dir = Path(__file__).parent / "fixtures"
    passthrough = _provider_passthrough(args)
    if args.preset:
        passthrough += ["--preset", args.preset]

    # Config axis: sweep one ReviewConfig setting on the current tree. Each value is
    # its own leg; we report each against the first as baseline.
    if args.sweep:
        try:
            field, values = _parse_sweep(args.sweep)
        except ValueError as exc:
            ap.error(str(exc))
        legs = [
            _current_leg(
                fixtures_dir,
                provider=args.provider,
                model=args.model,
                extra_args=passthrough + _sweep_args(field, value),
                label=f"{field}={value}",
            )
            for value in values
        ]
        for leg in legs[1:]:
            _print_report(ABReport(baseline=legs[0], current=leg))
        return 0

    if not args.baseline_ref:
        ap.error("either --baseline-ref or --sweep is required")

    baseline = _baseline_leg(
        args.baseline_ref,
        fixtures_dir,
        provider=args.provider,
        model=args.model,
        extra_args=passthrough,
    )
    current = _current_leg(
        fixtures_dir, provider=args.provider, model=args.model, extra_args=passthrough
    )
    report = ABReport(baseline=baseline, current=current)
    _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
