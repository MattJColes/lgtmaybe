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
    execute_local_review,
    execute_review,
    main,
    pr_url_from_event,
    resolve_auto_incremental,
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


@main.command()
@click.option(
    "--provider",
    default=None,
    help="LLM provider (openai, anthropic, bedrock, vertex, azure, ollama, "
    "openrouter, zai, openai-compatible)",
)
@click.option("--model", default=None, help="Model name understood by the chosen provider")
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
    "--timeout",
    default=None,
    type=int,
    help="Per-request timeout in seconds for each model call (raise for slow local models)",
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
    "--config",
    "config_path",
    default=".lgtmaybe.yml",
    show_default=True,
    help="Path to a per-repo config file",
)
def review(
    provider: str | None,
    model: str | None,
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
    base: str | None,
    working: bool,
    uncommitted: bool,
    output_format: str | None,
    as_json: bool,
    context_lines: int | None,
    timeout: int | None,
    temperature: float | None,
    reflect: bool | None,
    min_confidence: int | None,
    recursive: bool | None,
    structured_output: bool | None,
    symbol_resolution: bool | None,
    prompt_cache: bool | None,
    static_analysis: bool | None,
    config_path: str,
) -> None:
    """Review local git changes and print findings — no GitHub needed."""
    if working and uncommitted:
        raise click.UsageError("--working and --uncommitted are mutually exclusive")
    cfg = load_config(
        config_path=Path(config_path),
        user_config_path=store.user_config_path(),
        provider=provider,
        model=model,
        reflect_model=reflect_model,
        triage_model=triage_model,
        min_severity=min_severity,
        unanchored_min_severity=unanchored_min_severity,
        max_files=max_files,
        max_input_tokens=max_input_tokens,
        num_ctx=num_ctx,
        context_lines=context_lines,
        timeout=timeout,
        temperature=temperature,
        reflect=reflect,
        min_confidence=min_confidence,
        recursive=recursive,
        structured_output=structured_output,
        symbol_resolution=symbol_resolution,
        prompt_cache=prompt_cache,
    )
    cfg = _apply_static_analysis_flag(cfg, static_analysis)

    runtime = RuntimeOptions(api_key=api_key, api_base=api_base, fallback_model=fallback_model)
    fmt = output_format or ("json" if as_json else "human")
    execute_local_review(cfg, runtime, base=base, working=working, uncommitted=uncommitted, fmt=fmt)


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
@click.option("--config", "config_path", default=".lgtmaybe.yml", show_default=True)
def comment(
    event_path: str,
    provider: str | None,
    model: str | None,
    fallback_model: str | None,
    api_key: str | None,
    api_base: str | None,
    config_path: str,
) -> None:
    """Handle an issue_comment event: route a /slash command to the engine."""
    event = json.loads(Path(event_path).read_text())
    cfg = load_config(config_path=Path(config_path), provider=provider, model=model)
    runtime = RuntimeOptions(api_key=api_key, api_base=api_base, fallback_model=fallback_model)
    execute_comment(event, cfg, runtime)


@main.command()
def action() -> None:
    """GitHub Action entrypoint: route by event, read inputs from env.

    ``issue_comment`` routes a slash command; any other event (``pull_request``
    / ``pull_request_target``) runs a full review of the triggering PR.
    """
    inputs = action_inputs()
    cfg = load_config(
        config_path=Path(inputs["config_path"] or ".lgtmaybe.yml"),
        provider=inputs["provider"],
        model=inputs["model"],
        reflect_model=inputs["reflect_model"],
        triage_model=inputs["triage_model"],
        timeout=inputs["timeout"],
        temperature=inputs["temperature"],
        num_ctx=inputs["num_ctx"],
        max_input_tokens=inputs["max_input_tokens"],
        resolve_fixed=inputs["resolve_fixed"],
        recursive=inputs["recursive"],
        structured_output=inputs["structured_output"],
        symbol_resolution=inputs["symbol_resolution"],
        prompt_cache=inputs["prompt_cache"],
        incremental=inputs["incremental"],
    )
    raw_sa = inputs["static_analysis"]
    cfg = _apply_static_analysis_flag(
        cfg, None if raw_sa is None else raw_sa.strip().lower() in ("true", "1", "yes")
    )
    runtime = RuntimeOptions(
        api_key=inputs["api_key"],
        api_base=inputs["api_base"],
        fallback_model=inputs["fallback_model"],
    )

    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "issue_comment":
        execute_comment(event, cfg, runtime)
        return

    # incremental=None (auto): review only the new commits on a synchronize
    # push, do a full review on open/reopen. Explicit config/input wins.
    cfg = resolve_auto_incremental(cfg, event_action=str(event.get("action") or ""))
    runtime = replace(runtime, pr_url=pr_url_from_event(event))
    execute_review(cfg, runtime, dry_run=False)


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
