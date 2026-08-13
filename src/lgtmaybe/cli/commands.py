"""Click command + option declarations.

The callbacks are thin: they resolve config and a ``RuntimeOptions`` from the
flags / action inputs, then delegate to the ``execute_*`` functions in
``lgtmaybe.cli``. Imported by ``lgtmaybe.cli`` at import time so the commands
register onto the ``main`` / ``config`` groups defined there.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

import click

from lgtmaybe.cli import (
    _RUNTIME_INPUTS,
    RuntimeOptions,
    action_inputs,
    config_cmd,
    execute_comment,
    execute_local_diagram,
    execute_local_review,
    execute_review,
    execute_review_reply,
    main,
    pr_url_from_event,
    resolve_auto_incremental,
    should_auto_describe,
    should_auto_diagram,
)
from lgtmaybe.config import store
from lgtmaybe.config.loader import load_config
from lgtmaybe.core.models import Provider, ReviewConfig, ReviewPreset, Severity

F = TypeVar("F", bound=Callable[..., Any])


def _apply_static_analysis_flag(cfg: ReviewConfig, flag: bool | None) -> ReviewConfig:
    """Overlay the --static-analysis on/off flag onto the nested config block.

    The flag flips only ``static_analysis.enabled`` — the tool list and floors
    keep whatever `.lgtmaybe.yml` configured. None (flag not given) leaves the
    config untouched.
    """
    if flag is None:
        return cfg
    return cfg.model_copy(
        update={"static_analysis": cfg.static_analysis.model_copy(update={"enabled": flag})}
    )


def _parse_bool(value: str | None) -> bool | None:
    """Parse an action bool input the way pydantic parses the others.

    None (unset input) stays None so downstream defaults apply.
    """
    if value is None:
        return None
    return value.strip().lower() in ("true", "t", "1", "yes", "y", "on")


def _load_cfg(config_path: str | None, **inputs: Any) -> ReviewConfig:
    """Load config; an explicitly given path must exist and parse to a mapping.

    None (no --config / no config_path input) probes the default
    ``.lgtmaybe.yml`` leniently — absent is fine. Loader errors surface as a
    clean CLI error rather than a traceback.
    """
    try:
        return load_config(
            config_path=Path(config_path) if config_path else Path(".lgtmaybe.yml"),
            config_required=config_path is not None,
            **inputs,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _check_diff_mode(working: bool, uncommitted: bool) -> None:
    """Reject --working with --uncommitted, once, for both local commands.

    ``local_pr_context`` enforces the same invariant for library callers; the
    CLI checks it up front so the user gets a usage error (exit 2) before any
    provider work, rather than a runtime failure after it.
    """
    if working and uncommitted:
        raise click.UsageError("--working and --uncommitted are mutually exclusive")


def _runtime(
    api_key: str | None, api_base: str | None, fallback_model: str | None, profile: bool = False
) -> RuntimeOptions:
    """The RuntimeOptions every ``model_options`` command builds from its flags."""
    return RuntimeOptions(
        api_key=api_key, api_base=api_base, fallback_model=fallback_model, profile=profile
    )


def _stack(*options: Callable[..., Any]) -> Callable[[F], F]:
    """Bundle click options into one reusable decorator, applied bottom-up.

    A shared group then reads on the command exactly as the inline decorators
    did — and can only document itself one way across the commands that take it.
    """

    def wrap(func: F) -> F:
        for option in reversed(options):
            func = option(func)
        return func

    return wrap


#: The provider/model/credential flags every model-invoking command takes.
model_options = _stack(
    click.option(
        "--provider",
        default=None,
        # NOT `click.Choice(Provider)`: click matches an Enum by member NAME, so
        # that would accept `openai_compatible` and reject the documented
        # `openai-compatible`. The values keep the wire spelling, still derived.
        type=click.Choice([p.value for p in Provider]),
        # The nine names review used to spell out in prose are the metavar now,
        # so --help still shows them all — from the enum, not a second copy.
        help="LLM backend to review with (overrides the configured provider)",
    ),
    click.option("--model", default=None, help="Model name understood by the chosen provider"),
    click.option(
        "--fallback-model",
        default=None,
        help="Model to retry with if the primary model fails",
    ),
    click.option(
        "--api-key",
        default=None,
        envvar="LGTMAYBE_API_KEY",
        help="API key (not needed for bedrock/vertex/keyless-azure ambient creds, or ollama)",
    ),
    click.option(
        "--api-base",
        default=None,
        help="API base URL (ollama: http://localhost:11434; "
        "azure: https://<resource>.openai.azure.com; "
        "openai-compatible: any OpenAI /v1 endpoint, e.g. https://api.deepseek.com/v1 "
        "or http://localhost:8000/v1; "
        "zai: optional override for the China / coding-plan GLM endpoint, "
        "e.g. https://open.bigmodel.cn/api/paas/v4)",
    ),
    click.option(
        "--config",
        "config_path",
        default=None,
        help="Path to a per-repo config file (must exist when given) "
        "[default: .lgtmaybe.yml, absent is fine]",
    ),
)

#: The flags the two local (no-GitHub) commands share: what to diff, and the
#: model-call knobs a slow local model needs.
local_diff_options = _stack(
    click.option(
        "--base",
        default=None,
        help="Base ref to diff against (default: the remote's primary branch — "
        "origin/HEAD, else origin/main / origin/master, else a local main/master)",
    ),
    click.option(
        "--working",
        is_flag=True,
        default=False,
        help="Use the whole worktree — branch commits plus uncommitted edits — "
        "against the base, instead of only the committed branch changes",
    ),
    click.option(
        "--uncommitted",
        is_flag=True,
        default=False,
        help="Use only the uncommitted working-tree edits (vs HEAD); "
        "mutually exclusive with --working",
    ),
    click.option(
        "--timeout",
        default=None,
        type=int,
        help="Per-request timeout in seconds for each model call (raise for slow local models)",
    ),
    click.option(
        "--num-ctx",
        default=None,
        type=int,
        help="ollama context window (ollama only; ignored for hosted providers). "
        "Raise it so a large multi-file diff isn't truncated; default 32768",
    ),
)


@main.command()
@model_options
@local_diff_options
@click.option(
    "--preset",
    default=None,
    type=click.Choice(ReviewPreset),
    help="Review preset: fast (default) covers all nine categories in four "
    "calls, one per concern — security, correctness (stated intent folds in), "
    "code health, artefacts (tests/documentation) — the same four on every "
    "provider; full runs one call per lens for deep audits",
)
@click.option(
    "--full",
    "full_preset",
    is_flag=True,
    default=False,
    help="Shorthand for --preset full",
)
@click.option(
    "--reflect-model",
    default=None,
    help="Model for the self-reflection (false-positive audit) pass; defaults to "
    "--model. Point it at a stronger model to audit a weaker reviewer's findings",
)
@click.option(
    "--language",
    default=None,
    help="Human language for the reviewer's prose — finding title/body (and "
    "describe/diagram text). Structural fields and suggestion code stay "
    "unchanged. Unset = English",
)
@click.option(
    "--triage-model",
    default=None,
    help="Cheap model that runs first to skip plainly-non-substantive files and "
    "rank the rest by risk; the strong --model then reviews only the survivors. "
    "Security-relevant files always escalate past triage. Unset = no triage",
)
@click.option(
    "--min-severity",
    default=None,
    type=click.Choice(Severity),
    help="Minimum severity to report",
)
@click.option(
    "--fail-on",
    default=None,
    type=click.Choice(Severity),
    help="Merge-gate threshold: on the GitHub Action, create a Check Run that "
    "fails when any finding is at or above this severity (make it a required "
    "check in branch protection). Default off",
)
@click.option(
    "--unanchored-min-severity",
    default=None,
    type=click.Choice(Severity),
    help="Minimum severity for a finding the engine could not anchor to a changed "
    "line (default high; raise/lower to control how many low-confidence guesses surface)",
)
@click.option("--max-files", default=None, type=int, help="Maximum number of files to review")
@click.option(
    "--max-file-diff-lines",
    default=None,
    type=click.IntRange(min=0),
    help="Skip any single file whose diff is longer than this many lines "
    "(default 2000; 0 disables). Catches generated data blobs no name-based "
    "filter can recognise; every skip is named in the summary",
)
@click.option(
    "--max-input-tokens",
    default=None,
    type=int,
    help="Token budget per model call before the diff is split into batches "
    "(any provider; raise it to send a big diff in fewer calls)",
)
@click.option(
    "--max-tokens",
    default=None,
    type=click.IntRange(min=1),
    help="Cap the tokens each model call may generate (any provider; unset = the "
    "model's own ceiling). Set it on a prepaid route like OpenRouter, which "
    "reserves prompt + max_tokens against your balance before generating and "
    "assumes the model's full ceiling when the request omits it",
)
@click.option(
    "--reasoning-effort",
    default=None,
    type=click.Choice(["none", "minimal", "low", "medium", "high", "xhigh", "default"]),
    help="Bound what a reasoning model spends THINKING per call (unset = the "
    "route's own default). Use it when lens calls truncate or run long: "
    "max_tokens caps thinking and findings together, and the model spends the "
    "thinking first, so raising the cap grows the reasoning instead of the answer",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json", "agent"]),
    default=None,
    help="Output format: human listing (default), json array, or agent "
    "(correction instructions for an AI coding agent to read and apply).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Shorthand for --format json.",
)
@click.option(
    "--context-lines",
    default=None,
    type=int,
    help="Max unchanged lines added around each hunk for context (0 disables)",
)
@click.option(
    "--max-concurrency",
    default=None,
    type=click.IntRange(min=1),
    help="Concurrent review calls across the whole fan-out (all batches and "
    "lenses share one pool). Default: 8 for cloud providers, 1 for ollama, and "
    "1 for openai-compatible — a llama.cpp/LM Studio single-slot server wants 1, "
    "while a vLLM server batches happily at 8; raise it there explicitly",
)
@click.option(
    "--max-review-seconds",
    default=None,
    type=click.IntRange(min=0),
    help="Soft wall-clock ceiling for the whole review (default 3600). Past it, "
    "no further model calls are dispatched — in-flight calls finish and the "
    "review returns partial results with an incomplete-results notice. 0 disables",
)
@click.option(
    "--max-review-tokens",
    default=None,
    type=click.IntRange(min=0),
    help="Soft billable-token ceiling for the whole review — input + output "
    "across every model call (0, the default, disables it). Past it, no further "
    "model calls are dispatched and the review returns partial results with a "
    "notice. Run once with --profile to see a real total, then set this above it",
)
@click.option(
    "--temperature",
    default=None,
    type=float,
    help="Sampling temperature (default 0.0 for deterministic reviews)",
)
@click.option(
    "--reflect/--no-reflect",
    default=None,
    help="Run the self-reflection pass that drops low-confidence findings "
    "(--no-reflect keeps them all; useful for weaker models)",
)
@click.option(
    "--learn-feedback/--no-learn-feedback",
    default=None,
    help="On a re-run, suppress a finding a human reacted 👎 to on its inline "
    "comment last time (GitHub posting only; --no-learn-feedback disables). The "
    "reactions live on GitHub and are re-read each run — no local state",
)
@click.option(
    "--min-confidence",
    default=None,
    type=click.IntRange(0, 10),
    help="Drop findings the reflection auditor scores below this confidence "
    "(0-10; default 0 = no numeric filtering, unscored findings always survive)",
)
@click.option(
    "--recursive/--no-recursive",
    default=None,
    help="Walk a file whose diff exceeds the token budget hunk-by-hunk (RLM-style) "
    "instead of sending it whole and dropping the tail (--no-recursive disables)",
)
@click.option(
    "--structured-output/--no-structured-output",
    default=None,
    help="Constrain output to the findings JSON schema via response_format "
    "(--no-structured-output for a gateway/proxy that rejects it; the lenient "
    "parser still handles fenced/prose output either way)",
)
@click.option(
    "--mid-review-retrieval/--no-mid-review-retrieval",
    default=None,
    help="Let a review lens defer once for bounded read-only context: rather than "
    "hedging a finding that hinges on code outside the diff, it names the files or "
    "symbols it must read and is re-run with them (off by default — up to one extra "
    "model call per batch and lens)",
)
@click.option(
    "--symbol-resolution/--no-symbol-resolution",
    default=None,
    help="During reflection, use ast-grep to resolve a deferred finding's "
    "referenced symbol to the file that defines it (searched in your worktree) so "
    "the auditor re-judges with the real definition (--no-symbol-resolution disables)",
)
@click.option(
    "--spec/--no-spec",
    "spec_review",
    default=None,
    help="Check the diff against a specification the repository commits "
    "(OpenSpec, GitHub Spec Kit, Kiro): requirements it falls short of, task-list "
    "entries it ticks off without doing, and behaviour no requirement covers. "
    "On by default, but only runs when a spec is detected AND matches this PR — "
    "a repo without specs pays nothing (--no-spec disables)",
)
@click.option(
    "--static-analysis/--no-static-analysis",
    default=None,
    help="Run installed deterministic tools (ruff, bandit, mypy, gitleaks, zizmor, "
    "ast-grep, osv-scanner, semgrep) over the changed files: linters ground the model "
    "as untrusted hints to confirm or discard, while gitleaks, zizmor, ast-grep and "
    "osv-scanner post findings directly with no model call "
    "(default off; tools not installed are skipped silently — "
    "pip install lgtmaybe[static-analysis])",
)
@click.option(
    "--profile",
    is_flag=True,
    default=False,
    help="Print a timing profile at the end of the run: total wall time, "
    "per-stage and per-call tables, and prompt-cache hit totals",
)
def review(**inputs: Any) -> None:
    """Review local git changes and print findings — no GitHub needed."""
    working = inputs.pop("working")
    uncommitted = inputs.pop("uncommitted")
    _check_diff_mode(working, uncommitted)
    full_preset = inputs.pop("full_preset")
    preset = inputs.get("preset")
    if full_preset and preset is ReviewPreset.fast:
        raise click.UsageError("--full contradicts --preset fast")
    inputs["preset"] = ReviewPreset.full if full_preset else preset
    config_path = inputs.pop("config_path")
    runtime = _runtime(
        inputs.pop("api_key"),
        inputs.pop("api_base"),
        inputs.pop("fallback_model"),
        profile=inputs.pop("profile"),
    )
    static_analysis = inputs.pop("static_analysis")
    base = inputs.pop("base")
    output_format = inputs.pop("output_format")
    as_json = inputs.pop("as_json")
    cfg = _load_cfg(config_path, user_config_path=store.user_config_path(), **inputs)
    cfg = _apply_static_analysis_flag(cfg, static_analysis)

    fmt = output_format or ("json" if as_json else "human")
    execute_local_review(cfg, runtime, base=base, working=working, uncommitted=uncommitted, fmt=fmt)


@main.command()
@model_options
@local_diff_options
def diagram(
    provider: str | None,
    model: str | None,
    fallback_model: str | None,
    api_key: str | None,
    api_base: str | None,
    base: str | None,
    working: bool,
    uncommitted: bool,
    timeout: int | None,
    num_ctx: int | None,
    config_path: str | None,
) -> None:
    """Print a compact Mermaid diagram of your local changes — no GitHub needed.

    Emits the ASCII rendering (which shows in a terminal) plus the Mermaid
    source — paste that into a GitHub comment, mermaid.live, or a Markdown file
    to render it.
    """
    _check_diff_mode(working, uncommitted)
    cfg = _load_cfg(
        config_path,
        user_config_path=store.user_config_path(),
        provider=provider,
        model=model,
        timeout=timeout,
        num_ctx=num_ctx,
    )
    runtime = _runtime(api_key, api_base, fallback_model)
    execute_local_diagram(cfg, runtime, base=base, working=working, uncommitted=uncommitted)


@main.command()
@click.option(
    "--event-path",
    envvar="GITHUB_EVENT_PATH",
    required=True,
    help="Path to the issue_comment event payload (GitHub sets GITHUB_EVENT_PATH).",
)
@model_options
def comment(
    event_path: str,
    provider: str | None,
    model: str | None,
    fallback_model: str | None,
    api_key: str | None,
    api_base: str | None,
    config_path: str | None,
) -> None:
    """Handle an issue_comment event: route a /slash command to the engine."""
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    cfg = _load_cfg(config_path, provider=provider, model=model)
    runtime = _runtime(api_key, api_base, fallback_model)
    execute_comment(event, cfg, runtime)


@main.command()
def action() -> None:
    """GitHub Action entrypoint: route by event, read inputs from env.

    ``issue_comment`` routes a slash command; any other event (``pull_request``
    / ``pull_request_target``) runs a full review of the triggering PR.
    """
    inputs = action_inputs()
    cfg = _load_cfg(
        inputs["config_path"],
        **{key: value for key, value in inputs.items() if key not in _RUNTIME_INPUTS},
    )
    cfg = _apply_static_analysis_flag(cfg, _parse_bool(inputs["static_analysis"]))
    runtime = RuntimeOptions(
        api_key=inputs["api_key"],
        api_base=inputs["api_base"],
        fallback_model=inputs["fallback_model"],
        profile=bool(_parse_bool(inputs["profile"])),
    )

    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "issue_comment":
        execute_comment(event, cfg, runtime)
        return

    if event_name == "pull_request_review_comment":
        # A reply inside a review conversation — answered in-thread when it lands
        # on a finding lgtmaybe opened (loop-safe; see execute_review_reply).
        execute_review_reply(event, cfg, runtime)
        return

    # incremental=None (auto): review only the new commits on a synchronize
    # push, do a full review on open/reopen. Explicit config/input wins.
    event_action = str(event.get("action") or "")
    cfg = resolve_auto_incremental(cfg, event_action=event_action)
    runtime = replace(runtime, pr_url=pr_url_from_event(event))
    # Auto-description stays open/reopen-only; auto-diagram also refreshes on a
    # synchronize push so a replacement run cannot lose it. Both are best-effort
    # and post after the review. execute_review shares one gateway and one PR-context
    # fetch across the extras and the review itself.
    execute_review(
        cfg,
        runtime,
        describe=should_auto_describe(cfg, event_action=event_action),
        diagram=should_auto_diagram(cfg, event_action=event_action),
    )


@config_cmd.command("path")
def config_path_command() -> None:
    """Print the config file location."""
    click.echo(str(store.user_config_path()))


@config_cmd.command("show")
def config_show() -> None:
    """Print the current config."""
    text = store.as_yaml()
    click.echo(text if text else f"(no config yet at {store.user_config_path()})")


@config_cmd.command("get")
@click.argument("key")
def config_get(key: str) -> None:
    """Print one config value."""
    value = store.get_key(key)
    if value is not None:
        click.echo(str(value))


@config_cmd.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set one config value (e.g. `config set model qwen3:27b`)."""
    try:
        coerced = store.set_key(key, value)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{key} = {coerced}")


@config_cmd.command("init")
def config_init() -> None:
    """Interactively create the config file."""
    provider = click.prompt("Provider", default="ollama")
    model = click.prompt("Model", default="llama3")
    api_base = click.prompt("API base (blank for none)", default="", show_default=False)
    try:
        store.set_key("provider", provider)
        store.set_key("model", model)
        if api_base.strip():
            store.set_key("api_base", api_base)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Wrote {store.user_config_path()}")
