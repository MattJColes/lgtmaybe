"""Click command + option declarations.

The callbacks are thin: they resolve config and a ``RuntimeOptions`` from the
flags / action inputs, then delegate to the ``execute_*`` functions in
``lgtmaybe.cli``. Imported by ``lgtmaybe.cli`` at import time so the commands
register onto the ``main`` / ``config`` groups defined there.
"""

from __future__ import annotations

import json
import logging
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
    base_changed,
    build_provider_engine,
    config_cmd,
    execute_comment,
    execute_local_diagram,
    execute_local_review,
    execute_review,
    main,
    mr_url_from_ci_env,
    pr_event_action,
    pr_url_from_event,
    pr_url_from_pipelines_env,
    resolve_auto_incremental,
    resolve_ci_incremental,
    should_auto_diagram,
)
from lgtmaybe.config import detect, store
from lgtmaybe.config.loader import LOCAL_SETUP_HINT, ProviderNotConfigured, load_config
from lgtmaybe.core.models import Provider, ReviewConfig, ReviewPreset, Severity
from lgtmaybe.core.ports import ProviderTruncated
from lgtmaybe.providers.factory import DEFAULT_OLLAMA_BASE

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


def _apply_risk_flag(cfg: ReviewConfig, flag: bool | None) -> ReviewConfig:
    """Overlay the --risk on/off flag onto the nested config, keeping ``core_paths``."""
    if flag is None:
        return cfg
    return cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"enabled": flag})})


def _parse_bool(value: str | None) -> bool | None:
    """Parse an action bool input the way pydantic parses the others.

    None (unset input) stays None so downstream defaults apply.
    """
    if value is None:
        return None
    return value.strip().lower() in ("true", "t", "1", "yes", "y", "on")


#: How each CI entrypoint says "configure a provider". Neither has a user
#: config file or a `config init` to run, so the local pointer would mislead.
_ACTION_SETUP_HINT = (
    "set the action's `provider` and `model` inputs in your workflow's `with:` block, "
    "or add them to .lgtmaybe.yml."
)
_FLAGS_SETUP_HINT = "pass `--provider`/`--model`, or add them to .lgtmaybe.yml."


def _load_cfg(
    config_path: str | None,
    *,
    setup_hint: str = LOCAL_SETUP_HINT,
    **inputs: Any,
) -> ReviewConfig:
    """Load config; an explicitly given path must exist and parse to a mapping.

    None (no --config / no config_path input) probes the default
    ``.lgtmaybe.yml`` leniently — absent is fine. Loader errors surface as a
    clean CLI error rather than a traceback. ``setup_hint`` is the remedy shown
    when no layer configures a provider/model, phrased for the entrypoint.
    """
    try:
        return load_config(
            config_path=Path(config_path) if config_path else Path(".lgtmaybe.yml"),
            config_required=config_path is not None,
            **inputs,
        )
    except ProviderNotConfigured as exc:
        raise click.ClickException(exc.message(setup_hint)) from exc
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
    api_key: str | None,
    api_base: str | None,
    fallback_model: str | None,
    profile: bool = False,
    profile_json: Path | None = None,
) -> RuntimeOptions:
    """The RuntimeOptions every ``model_options`` command builds from its flags."""
    return RuntimeOptions(
        api_key=api_key,
        api_base=api_base,
        fallback_model=fallback_model,
        profile=profile,
        profile_json=profile_json,
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


class ProgressiveHelpCommand(click.Command):
    """A command whose ``--help`` shows only its everyday options.

    ``common`` names the parameters a first-time user reaches for; every other
    option is tuning, listed only by ``--help-all`` and summarised in the short
    help by one trailing line. Rendering only: every option parses exactly the
    same either way.
    """

    def __init__(self, *args: Any, common: frozenset[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.common = common | {"help_all"}

    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        common: list[tuple[str, str]] = []
        advanced: list[tuple[str, str]] = []
        for param in self.get_params(ctx):
            record = param.get_help_record(ctx)
            if record is not None:
                is_common = param.name in self.common or "--help" in param.opts
                (common if is_common else advanced).append(record)
        if ctx.meta.get(_HELP_ALL_KEY):
            for title, records in (("Common options", common), ("Advanced options", advanced)):
                with formatter.section(title):
                    formatter.write_dl(records)
            return
        with formatter.section("Options"):
            formatter.write_dl(common)
        formatter.write_paragraph()
        formatter.write_text(
            f"{len(advanced)} advanced tuning options: run `{ctx.command_path} --help-all`"
        )


_HELP_ALL_KEY = "lgtmaybe.help_all"


def _show_help_all(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    """Eager ``--help-all``: print the full option reference, then exit."""
    if not value or ctx.resilient_parsing:
        return
    ctx.meta[_HELP_ALL_KEY] = True
    click.echo(ctx.get_help(), color=ctx.color)
    ctx.exit()


help_all_option = click.option(
    "--help-all",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_show_help_all,
    help="Show every option, including the advanced tuning ones, and exit.",
)

#: The options `review --help` shows; the rest wait for `--help-all`.
_COMMON_REVIEW_OPTIONS = frozenset(
    {
        "provider",
        "model",
        "base",
        "working",
        "uncommitted",
        "output_format",
        "as_json",
        "min_severity",
        "full_preset",
        "config_path",
    }
)


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

#: The model-call knobs a slow local model needs, for every local command.
model_call_options = _stack(
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

#: The flags the two local (no-GitHub) commands share: what to diff, and the
#: model-call knobs above.
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
    model_call_options,
)


@main.command(cls=ProgressiveHelpCommand, common=_COMMON_REVIEW_OPTIONS)
@model_options
@local_diff_options
@click.option(
    "--preset",
    default=None,
    type=click.Choice(ReviewPreset),
    help="Review preset: fast (default) covers all ten categories in five "
    "calls, one per concern — security, correctness (stated intent folds in), "
    "code health, artefacts (tests/documentation), architecture — the same five "
    "on every provider; full runs one call per lens for deep audits",
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
    help="Fail when any finding is at or above this severity. Locally, exit 3 "
    "after printing (for a pre-commit/pre-push hook or a script); on the GitHub "
    "Action, create a Check Run that fails (make it a required check in branch "
    "protection). Default off",
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
    type=click.IntRange(min=0),
    help="Cap the tokens each model call may generate (any provider; 0 = no cap). "
    "Unset means ollama, openai-compatible and openrouter get a finite default "
    "ceiling (16384) — a local model can otherwise decode until the timeout, and "
    "OpenRouter reserves prompt + max_tokens against a prepaid balance — while "
    "every other route keeps the model's own",
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
    type=click.Choice(["human", "json", "agent", "sarif"]),
    default=None,
    help="Output format: human listing (default), json array, agent "
    "(correction instructions for an AI coding agent to read and apply), or "
    "sarif (a SARIF 2.1.0 log for GitHub code scanning or any SARIF viewer).",
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
    "lenses share one pool). Default: 6 for every provider. Set 1 for a "
    "single-slot llama.cpp/LM Studio server; raise it on a high rate tier or a "
    "batching vLLM server",
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
    "--repair-unparseable/--no-repair-unparseable",
    default=None,
    help="When a review call's reply can't be parsed into findings, send the reply "
    "back once — with the schema, without the diff — asking for it in the required "
    "shape (on by default; --no-repair-unparseable disables). Costs nothing on a "
    "healthy run: it fires only on a call that already failed",
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
    "ast-grep, osv-scanner, semgrep) over the changed files in a sandbox with no "
    "network. Every tool's output grounds the model as untrusted hints to confirm, "
    "contextualise or discard; set static_analysis.tool_mode to make a tool post "
    "directly (default on; tools not installed are skipped silently, so "
    "pip install lgtmaybe[static-analysis] to get them; --no-static-analysis disables)",
)
@click.option(
    "--risk/--no-risk",
    default=None,
    help="Assess the change's risk — low, medium, high or critical — from how many "
    "files import each changed file, sensitive paths, change size, and code changed "
    "without tests, and print it on the summary line. Deterministic, no model call "
    "(default on; --no-risk disables)",
)
@click.option(
    "--profile-json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the profile as JSON to PATH — per-call tokens, timings, findings "
    "and errors, with an unknown reasoning count as null rather than a `-` a "
    "parser has to string-match. A file, not a stream, so it never collides with "
    "--json or --agent output",
)
@click.option(
    "--profile",
    is_flag=True,
    default=False,
    help="Print a timing profile at the end of the run: total wall time, "
    "per-stage and per-call tables, and prompt-cache hit totals",
)
@help_all_option
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
        profile_json=inputs.pop("profile_json"),
    )
    static_analysis = inputs.pop("static_analysis")
    risk = inputs.pop("risk")
    base = inputs.pop("base")
    output_format = inputs.pop("output_format")
    as_json = inputs.pop("as_json")
    cfg = _load_cfg(config_path, user_config_path=store.user_config_path(), **inputs)
    cfg = _apply_static_analysis_flag(cfg, static_analysis)
    cfg = _apply_risk_flag(cfg, risk)

    fmt = output_format or ("json" if as_json else "human")
    execute_local_review(
        cfg,
        runtime,
        base=base,
        working=working,
        uncommitted=uncommitted,
        fmt=fmt,
        fail_on=inputs["fail_on"],
    )


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
    """Print the change overview of your local changes — no GitHub needed.

    A description of the change, its High Impact Areas, and the diagrams: the
    text rendering (which shows in a terminal) plus the Mermaid source — paste
    that into a GitHub comment, mermaid.live, or a Markdown file to render it.
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


@main.command("mcp")
@model_options
@model_call_options
@click.option(
    "--directory",
    "-C",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository to review, as if the server started there (like `git -C`) "
    "[default: the directory the server starts in]",
)
def mcp_command(
    provider: str | None,
    model: str | None,
    fallback_model: str | None,
    api_key: str | None,
    api_base: str | None,
    config_path: str | None,
    timeout: int | None,
    num_ctx: int | None,
    directory: Path | None,
) -> None:
    """Serve local reviews to a coding agent over MCP (stdio).

    For Claude Code, Cursor and other Model Context Protocol clients: the agent
    calls the review_changes tool (findings as JSON) or review_instructions
    (fixes to apply) instead of running `lgtmaybe review` itself. The flags here
    apply to every call; config and credentials load as they do for `review`.
    Needs the MCP SDK: pip install 'lgtmaybe[mcp]'.
    """
    from lgtmaybe.cli import mcp_server

    mcp_server.require_sdk()
    if directory is not None:
        os.chdir(directory)

    def load_config(**overrides: Any) -> ReviewConfig:
        return _load_cfg(
            config_path,
            user_config_path=store.user_config_path(),
            provider=provider,
            model=model,
            timeout=timeout,
            num_ctx=num_ctx,
            **overrides,
        )

    # Fail at startup on a broken config, where the client shows the error,
    # rather than on every tool call.
    load_config()
    runtime = _runtime(api_key, api_base, fallback_model)
    mcp_server.serve(mcp_server.LocalReviewTools(load_config, runtime))


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
    cfg = _load_cfg(config_path, setup_hint=_FLAGS_SETUP_HINT, provider=provider, model=model)
    runtime = _runtime(api_key, api_base, fallback_model)
    execute_comment(event, cfg, runtime)


@main.command()
def action() -> None:
    """GitHub Action entrypoint: route by event, read inputs from env.

    ``issue_comment`` routes a slash command, stale review-comment events are a
    no-op, and pull-request events run a review of the triggering PR.
    """
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "pull_request_review_comment":
        return

    inputs = action_inputs()
    cfg = _load_cfg(
        inputs["config_path"],
        setup_hint=_ACTION_SETUP_HINT,
        **{key: value for key, value in inputs.items() if key not in _RUNTIME_INPUTS},
    )
    cfg = _apply_static_analysis_flag(cfg, _parse_bool(inputs["static_analysis"]))
    cfg = _apply_risk_flag(cfg, _parse_bool(inputs["risk"]))
    runtime = RuntimeOptions(
        api_key=inputs["api_key"],
        api_base=inputs["api_base"],
        fallback_model=inputs["fallback_model"],
        profile=bool(_parse_bool(inputs["profile"])),
    )

    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    if event_name == "issue_comment":
        execute_comment(event, cfg, runtime)
        return

    event_action = pr_event_action(event)
    if event_action == "edited" and not base_changed(event):
        # Workflows listen for `edited` to catch a retarget; a title or
        # description edit changes no code, so it costs no review.
        click.echo("Pull request edited, but its base branch did not change: nothing to review.")
        return
    # incremental=None (auto): review only the new commits on a synchronize
    # push, do a full review on open/reopen and on a retarget (same head, new
    # diff). Explicit config/input wins.
    cfg = resolve_auto_incremental(cfg, event_action=event_action)
    runtime = replace(runtime, pr_url=pr_url_from_event(event))
    # The change overview refreshes on a synchronize push too, so a replacement
    # run cannot lose it. It posts after the review, and execute_review shares
    # one gateway and one PR-context fetch across the two.
    execute_review(cfg, runtime, diagram=should_auto_diagram(cfg, event_action=event_action))


@main.command("gitlab-ci")
@model_options
@click.option(
    "--incremental/--no-incremental",
    default=None,
    envvar="LGTMAYBE_INCREMENTAL",
    help="Review only the commits since the last completed review (default: on whenever "
    "a watermark exists). --no-incremental, or LGTMAYBE_INCREMENTAL=false on a manual "
    "pipeline, forces a full re-review.",
)
def gitlab_ci(
    provider: str | None,
    model: str | None,
    fallback_model: str | None,
    api_key: str | None,
    api_base: str | None,
    config_path: str | None,
    incremental: bool | None,
) -> None:
    """GitLab CI entrypoint: review the merge request this pipeline is for.

    The counterpart to ``action``. GitLab has no event payload file and no
    ``INPUT_*`` convention, so the merge request comes from the predefined
    ``CI_*`` variables and configuration comes from the ordinary flags,
    environment, and ``.lgtmaybe.yml`` — everything downstream is shared.
    """
    cfg = resolve_ci_incremental(
        _load_cfg(
            config_path,
            setup_hint=_FLAGS_SETUP_HINT,
            provider=provider,
            model=model,
            incremental=incremental,
        )
    )
    runtime = replace(_runtime(api_key, api_base, fallback_model), pr_url=mr_url_from_ci_env())
    execute_review(cfg, runtime)


@main.command("azure-pipelines")
@model_options
def azure_pipelines(
    provider: str | None,
    model: str | None,
    fallback_model: str | None,
    api_key: str | None,
    api_base: str | None,
    config_path: str | None,
) -> None:
    """Azure Pipelines entrypoint: review the Azure Repos pull request this build validates.

    The counterpart to ``gitlab-ci``. Azure Pipelines has no event payload, so
    the pull request comes from the predefined ``SYSTEM_*`` and ``BUILD_*``
    variables; authenticate with ``AZURE_DEVOPS_TOKEN`` (a personal access
    token) or the job's own ``SYSTEM_ACCESSTOKEN`` mapped into the environment.
    """
    cfg = _load_cfg(config_path, provider=provider, model=model)
    runtime = replace(
        _runtime(api_key, api_base, fallback_model), pr_url=pr_url_from_pipelines_env()
    )
    execute_review(cfg, runtime)


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
    try:
        value = store.get_key(key)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
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


#: Generous enough for ollama to load a model cold, short enough that a stuck
#: endpoint does not leave the wizard hanging for the review-sized default.
_VERIFY_TIMEOUT = 180

#: Where each provider that needs an endpoint reads it from the environment.
_BASE_ENV: dict[Provider, str] = {
    Provider.azure: "AZURE_API_BASE",
    Provider.openai_compatible: "OPENAI_COMPATIBLE_API_BASE",
}


def _parse_provider(value: str) -> Provider:
    """A provider from its listed number or its name; re-prompts on anything else."""
    text = value.strip()
    providers = list(Provider)
    if text.isdigit() and 1 <= int(text) <= len(providers):
        return providers[int(text) - 1]
    try:
        return Provider(text)
    except ValueError:
        names = ", ".join(p.value for p in providers)
        raise click.BadParameter(f"unknown provider {text!r}; choose one of: {names}") from None


def _choose_provider(found: list[detect.Detection]) -> tuple[Provider, detect.Detection | None]:
    """Offer the strongest detection; fall back to the numbered list."""
    if found:
        if len(found) > 1:
            click.echo("Detected:")
            for item in found:
                click.echo(f"  {item.provider.value}: {item.reason}")
        first = found[0]
        if click.confirm(f"Found {first.reason} - use {first.provider.value}?", default=True):
            return first.provider, first
    else:
        click.echo("No provider API key, cloud credentials or running ollama detected.")
    for number, provider in enumerate(Provider, start=1):
        click.echo(f"  {number}. {provider.value}")
    chosen: Provider = click.prompt("Provider (number or name)", value_proc=_parse_provider)
    return chosen, next((item for item in found if item.provider is chosen), None)


def _choose_api_base(
    provider: Provider, found: detect.Detection | None
) -> tuple[str | None, tuple[str, ...]]:
    """The endpoint to store (None = the provider's own default), plus any
    models a running ollama there has pulled."""
    if provider is Provider.ollama:
        if found is not None:
            return None, found.models
        base: str = click.prompt("ollama API base", default=DEFAULT_OLLAMA_BASE)
        base = base.strip().rstrip("/")
        models = detect.ollama_models(base)
        if models is None:
            click.echo(f"No ollama answered at {base}; start it with `ollama serve`.")
        return (None if base == DEFAULT_OLLAMA_BASE else base), models or ()
    env_var = _BASE_ENV.get(provider)
    if env_var is None or os.environ.get(env_var):
        # Not needed, or the resolver will read it from the environment itself.
        return None, ()
    hint = (
        "https://<resource>.openai.azure.com"
        if provider is Provider.azure
        else "an OpenAI /v1 base URL, e.g. http://localhost:8000/v1"
    )
    entered: str = click.prompt(f"{provider.value} endpoint ({hint})")
    return entered.strip(), ()


def _choose_model(provider: Provider, pulled: tuple[str, ...]) -> str:
    """Prompt for the model, defaulting to a current suggestion when there is one."""
    if provider is Provider.ollama:
        if pulled:
            click.echo(f"Pulled models: {', '.join(pulled)}")
        else:
            click.echo(
                "No pulled models found; pull one first, e.g. "
                f"`ollama pull {detect.SUGGESTED_MODELS[Provider.ollama]}`."
            )
    label = {
        Provider.azure: "Deployment name",
        Provider.openai_compatible: "Model (the name the server serves)",
    }.get(provider, "Model")
    suggested = detect.suggest_model(provider, pulled)
    model: str = click.prompt(label, default=suggested) if suggested else click.prompt(label)
    return model.strip()


def _verify_setup(provider: Provider, model: str, api_base: str | None) -> None:
    """One tiny completion through the review's own credential + provider path,
    so a bad key or model fails now, with the same "how to auth" message."""
    click.echo(f"Checking {provider.value} / {model} with one short request...")
    cfg = ReviewConfig(provider=provider, model=model, api_base=api_base, timeout=_VERIFY_TIMEOUT)
    # The review's structured INFO logs would interleave with the prompts.
    logging.disable(logging.INFO)
    try:
        _engine, client = build_provider_engine(cfg, RuntimeOptions(api_base=api_base))
        client.complete([{"role": "user", "content": "Reply with the single word: ok"}], model)
    except ProviderTruncated:
        pass  # It answered: a reasoning model may spend the budget thinking.
    except Exception as exc:
        raise click.ClickException(
            f"{exc}\nNothing was written. Fix the above and rerun `lgtmaybe config init`, "
            "or pass --no-verify to write the config without checking."
        ) from exc
    finally:
        logging.disable(logging.NOTSET)
    click.echo("OK: the model answered.")


@config_cmd.command("init")
@click.option(
    "--verify/--no-verify",
    default=True,
    help="Check the provider and model with one tiny completion before writing "
    "(default on; --no-verify writes without a model call)",
)
def config_init(verify: bool) -> None:
    """Detect your credentials and a running ollama, then write the config file.

    API keys are never written: they stay in your environment.
    """
    provider, found = _choose_provider(detect.detect())
    api_base, pulled = _choose_api_base(provider, found)
    model = _choose_model(provider, pulled)
    if verify:
        _verify_setup(provider, model, api_base)

    data = store.load()
    data.update(provider=provider.value, model=model)
    if api_base:
        data["api_base"] = api_base
    else:
        # A base kept from an earlier setup would be sent to the new provider.
        data.pop("api_base", None)
    store.save(data)
    click.echo(f"Wrote {store.user_config_path()}")
    click.echo("Next: lgtmaybe review")
