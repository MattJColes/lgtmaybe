"""Click command + option declarations.

The callbacks are thin: they resolve config and a ``RuntimeOptions`` from the
flags / action inputs, then delegate to the ``execute_*`` functions in
``lgtmaybe.cli``. Imported by ``lgtmaybe.cli`` at import time so the commands
register onto the ``main`` / ``config`` groups defined there.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import click

from lgtmaybe.cli import (
    RuntimeOptions,
    action_inputs,
    config_cmd,
    execute_comment,
    execute_local_diagram,
    execute_local_review,
    execute_review,
    main,
    pr_url_from_event,
    resolve_auto_incremental,
    should_auto_describe,
    should_auto_diagram,
)
from lgtmaybe.config import store
from lgtmaybe.config.loader import load_config
from lgtmaybe.core.models import ReviewConfig


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


def _load_cfg(config_path: str | None, **inputs: object) -> ReviewConfig:
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


@main.command()
@click.option(
    "--provider",
    default=None,
    help="LLM provider (openai, anthropic, bedrock, vertex, azure, ollama, "
    "openrouter, zai, openai-compatible)",
)
@click.option("--model", default=None, help="Model name understood by the chosen provider")
@click.option(
    "--preset",
    default=None,
    type=click.Choice(["fast", "full"]),
    help="Review preset: fast (default) covers security, correctness/intent, "
    "performance, complexity, ponytail, and deprecation in four calls when "
    "parallelism is available, or three with one worker; full restores "
    "tests/documentation and runs one call per lens for deep audits",
)
@click.option(
    "--full",
    "full_preset",
    is_flag=True,
    default=False,
    help="Shorthand for --preset full",
)
@click.option(
    "--fallback-model",
    default=None,
    help="Model to retry with if the primary model fails",
)
@click.option(
    "--reflect-model",
    default=None,
    help="Model for the self-reflection (false-positive audit) pass; defaults to "
    "--model. Point it at a stronger model to audit a weaker reviewer's findings",
)
@click.option(
    "--triage-model",
    default=None,
    help="Cheap model that runs first to skip plainly-non-substantive files and "
    "rank the rest by risk; the strong --model then reviews only the survivors. "
    "Security-relevant files always escalate past triage. Unset = no triage",
)
@click.option(
    "--api-key",
    default=None,
    envvar="LGTMAYBE_API_KEY",
    help="API key (not needed for bedrock/vertex/keyless-azure ambient creds, or ollama)",
)
@click.option(
    "--api-base",
    default=None,
    help="API base URL (ollama: http://localhost:11434; "
    "azure: https://<resource>.openai.azure.com; "
    "openai-compatible: any OpenAI /v1 endpoint, e.g. https://api.deepseek.com/v1 "
    "or http://localhost:8000/v1; "
    "zai: optional override for the China / coding-plan GLM endpoint, "
    "e.g. https://open.bigmodel.cn/api/paas/v4)",
)
@click.option(
    "--min-severity",
    default=None,
    type=click.Choice(["info", "low", "medium", "high", "critical"]),
    help="Minimum severity to report",
)
@click.option(
    "--unanchored-min-severity",
    default=None,
    type=click.Choice(["info", "low", "medium", "high", "critical"]),
    help="Minimum severity for a finding the engine could not anchor to a changed "
    "line (default high; raise/lower to control how many low-confidence guesses surface)",
)
@click.option("--max-files", default=None, type=int, help="Maximum number of files to review")
@click.option(
    "--max-input-tokens",
    default=None,
    type=int,
    help="Token budget per model call before the diff is split into batches "
    "(any provider; raise it to send a big diff in fewer calls)",
)
@click.option(
    "--num-ctx",
    default=None,
    type=int,
    help="ollama context window (ollama only; ignored for hosted providers). "
    "Raise it so a large multi-file diff isn't truncated; default 32768",
)
@click.option(
    "--base",
    default=None,
    help="Base ref to diff against (default: the remote's primary branch — "
    "origin/HEAD, else origin/main / origin/master, else a local main/master)",
)
@click.option(
    "--working",
    is_flag=True,
    default=False,
    help="Review the whole worktree — branch commits plus uncommitted edits — "
    "against the base, instead of only the committed branch changes",
)
@click.option(
    "--uncommitted",
    is_flag=True,
    default=False,
    help="Review only the uncommitted working-tree edits (vs HEAD); "
    "mutually exclusive with --working",
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
    "--timeout",
    default=None,
    type=int,
    help="Per-request timeout in seconds for each model call (raise for slow local models)",
)
@click.option(
    "--max-review-seconds",
    default=None,
    type=click.IntRange(min=0),
    help="Soft wall-clock ceiling for the whole review (default 600). Past it, "
    "no further model calls are dispatched — in-flight calls finish and the "
    "review returns partial results with an incomplete-results notice. 0 disables",
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
    "--symbol-resolution/--no-symbol-resolution",
    default=None,
    help="During reflection, use ast-grep to resolve a deferred finding's "
    "referenced symbol to the file that defines it (searched in your worktree) so "
    "the auditor re-judges with the real definition (--no-symbol-resolution disables)",
)
@click.option(
    "--prompt-cache/--no-prompt-cache",
    default=None,
    help="Cache the static system prompt across the per-lens calls on providers "
    "that support it (anthropic, bedrock Claude/Nova) — cached reads are billed "
    "at a steep discount. Safe no-op elsewhere (--no-prompt-cache disables)",
)
@click.option(
    "--static-analysis/--no-static-analysis",
    default=None,
    help="Run installed deterministic linters (ruff, bandit, semgrep with local "
    "rules) over the changed files and feed their findings to the model as "
    "untrusted hints to confirm or discard (default off; tools not installed "
    "are skipped silently — pip install lgtmaybe[static-analysis])",
)
@click.option(
    "--profile",
    is_flag=True,
    default=False,
    help="Print a timing profile at the end of the run: total wall time, "
    "per-stage and per-call tables, and prompt-cache hit totals",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to a per-repo config file (must exist when given) "
    "[default: .lgtmaybe.yml, absent is fine]",
)
def review(
    provider: str | None,
    model: str | None,
    preset: str | None,
    full_preset: bool,
    fallback_model: str | None,
    reflect_model: str | None,
    triage_model: str | None,
    api_key: str | None,
    api_base: str | None,
    min_severity: str | None,
    unanchored_min_severity: str | None,
    max_files: int | None,
    max_input_tokens: int | None,
    num_ctx: int | None,
    max_concurrency: int | None,
    base: str | None,
    working: bool,
    uncommitted: bool,
    output_format: str | None,
    as_json: bool,
    context_lines: int | None,
    timeout: int | None,
    max_review_seconds: int | None,
    temperature: float | None,
    reflect: bool | None,
    min_confidence: int | None,
    recursive: bool | None,
    structured_output: bool | None,
    symbol_resolution: bool | None,
    prompt_cache: bool | None,
    static_analysis: bool | None,
    profile: bool,
    config_path: str | None,
) -> None:
    """Review local git changes and print findings — no GitHub needed."""
    if working and uncommitted:
        raise click.UsageError("--working and --uncommitted are mutually exclusive")
    if full_preset and preset == "fast":
        raise click.UsageError("--full contradicts --preset fast")
    cfg = _load_cfg(
        config_path,
        user_config_path=store.user_config_path(),
        provider=provider,
        model=model,
        preset="full" if full_preset else preset,
        reflect_model=reflect_model,
        triage_model=triage_model,
        min_severity=min_severity,
        unanchored_min_severity=unanchored_min_severity,
        max_files=max_files,
        max_input_tokens=max_input_tokens,
        num_ctx=num_ctx,
        max_concurrency=max_concurrency,
        context_lines=context_lines,
        timeout=timeout,
        max_review_seconds=max_review_seconds,
        temperature=temperature,
        reflect=reflect,
        min_confidence=min_confidence,
        recursive=recursive,
        structured_output=structured_output,
        symbol_resolution=symbol_resolution,
        prompt_cache=prompt_cache,
    )
    cfg = _apply_static_analysis_flag(cfg, static_analysis)

    runtime = RuntimeOptions(
        api_key=api_key, api_base=api_base, fallback_model=fallback_model, profile=profile
    )
    fmt = output_format or ("json" if as_json else "human")
    execute_local_review(cfg, runtime, base=base, working=working, uncommitted=uncommitted, fmt=fmt)


@main.command()
@click.option("--provider", default=None, help="LLM provider override")
@click.option("--model", default=None, help="Model name understood by the chosen provider")
@click.option("--fallback-model", default=None, help="Model to retry with if the primary fails")
@click.option(
    "--api-key",
    default=None,
    envvar="LGTMAYBE_API_KEY",
    help="API key (not needed for bedrock/vertex/keyless-azure ambient creds, or ollama)",
)
@click.option("--api-base", default=None, help="API base URL (e.g. ollama)")
@click.option(
    "--base",
    default=None,
    help="Base ref to diff against (default: the remote's primary branch)",
)
@click.option(
    "--working",
    is_flag=True,
    default=False,
    help="Diagram the whole worktree — branch commits plus uncommitted edits — vs the base",
)
@click.option(
    "--uncommitted",
    is_flag=True,
    default=False,
    help="Diagram only the uncommitted working-tree edits (vs HEAD); "
    "mutually exclusive with --working",
)
@click.option(
    "--timeout",
    default=None,
    type=int,
    help="Per-request timeout in seconds for the model call (raise for slow local models)",
)
@click.option(
    "--num-ctx",
    default=None,
    type=int,
    help="ollama context window (ollama only; raise it for a large multi-file diff)",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to a per-repo config file (must exist when given) "
    "[default: .lgtmaybe.yml, absent is fine]",
)
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
    """Print a C4-style diagram of your local changes — no GitHub needed.

    Emits the ASCII rendering (which shows in a terminal) plus the Mermaid
    source — paste that into a GitHub comment, mermaid.live, or a Markdown file
    to render it.
    """
    if working and uncommitted:
        raise click.UsageError("--working and --uncommitted are mutually exclusive")
    cfg = _load_cfg(
        config_path,
        user_config_path=store.user_config_path(),
        provider=provider,
        model=model,
        timeout=timeout,
        num_ctx=num_ctx,
    )
    runtime = RuntimeOptions(api_key=api_key, api_base=api_base, fallback_model=fallback_model)
    execute_local_diagram(cfg, runtime, base=base, working=working, uncommitted=uncommitted)


@main.command()
@click.option(
    "--event-path",
    envvar="GITHUB_EVENT_PATH",
    required=True,
    help="Path to the issue_comment event payload (GitHub sets GITHUB_EVENT_PATH).",
)
@click.option("--provider", default=None, help="LLM provider override")
@click.option("--model", default=None, help="Model name override")
@click.option("--fallback-model", default=None, help="Model to retry with if the primary fails")
@click.option("--api-key", default=None, envvar="LGTMAYBE_API_KEY", help="API key")
@click.option("--api-base", default=None, help="API base URL (e.g. ollama)")
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to a per-repo config file (must exist when given) "
    "[default: .lgtmaybe.yml, absent is fine]",
)
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
    event = json.loads(Path(event_path).read_text())
    cfg = _load_cfg(config_path, provider=provider, model=model)
    runtime = RuntimeOptions(api_key=api_key, api_base=api_base, fallback_model=fallback_model)
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
        provider=inputs["provider"],
        model=inputs["model"],
        preset=inputs["preset"],
        reflect_model=inputs["reflect_model"],
        triage_model=inputs["triage_model"],
        timeout=inputs["timeout"],
        max_review_seconds=inputs["max_review_seconds"],
        temperature=inputs["temperature"],
        num_ctx=inputs["num_ctx"],
        max_input_tokens=inputs["max_input_tokens"],
        max_concurrency=inputs["max_concurrency"],
        resolve_fixed=inputs["resolve_fixed"],
        recursive=inputs["recursive"],
        structured_output=inputs["structured_output"],
        symbol_resolution=inputs["symbol_resolution"],
        prompt_cache=inputs["prompt_cache"],
        incremental=inputs["incremental"],
        auto_describe=inputs["auto_describe"],
        auto_diagram=inputs["auto_diagram"],
        pr_labels=inputs["pr_labels"],
    )
    cfg = _apply_static_analysis_flag(cfg, _parse_bool(inputs["static_analysis"]))
    runtime = RuntimeOptions(
        api_key=inputs["api_key"],
        api_base=inputs["api_base"],
        fallback_model=inputs["fallback_model"],
        profile=bool(_parse_bool(inputs["profile"])),
    )

    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "issue_comment":
        execute_comment(event, cfg, runtime)
        return

    # incremental=None (auto): review only the new commits on a synchronize
    # push, do a full review on open/reopen. Explicit config/input wins.
    event_action = str(event.get("action") or "")
    cfg = resolve_auto_incremental(cfg, event_action=event_action)
    runtime = replace(runtime, pr_url=pr_url_from_event(event))
    # Auto-describe / auto-diagram (opt-in): on a freshly opened PR, post the
    # structured description and/or the change diagram first — both best-effort,
    # neither blocks the review. execute_review shares one gateway and one
    # PR-context fetch across the extras and the review itself.
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


@main.command(name="help")
@click.argument("command_path", nargs=-1, metavar="[COMMAND]...")
@click.pass_context
def help_command(ctx: click.Context, command_path: tuple[str, ...]) -> None:
    """Show help for lgtmaybe or a specific command.

    `lgtmaybe help review` shows the full option reference for `review`;
    nested paths work too: `lgtmaybe help config set`.
    """
    # Walk the command tree from the main group, rebuilding the context chain
    # so the usage line matches `lgtmaybe <command...> --help` exactly.
    target_ctx = ctx.parent if ctx.parent is not None else ctx
    cmd = target_ctx.command
    for name in command_path:
        sub = cmd.get_command(target_ctx, name) if isinstance(cmd, click.Group) else None
        if sub is None:
            raise click.UsageError(
                f"No such command {name!r}. Run `lgtmaybe help` to list commands."
            )
        target_ctx = click.Context(sub, info_name=name, parent=target_ctx)
        cmd = sub
    click.echo(cmd.get_help(target_ctx))
