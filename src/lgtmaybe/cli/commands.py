"""Click command + option declarations.

The callbacks are thin: they resolve config and a ``RuntimeOptions`` from the
flags / action inputs, then delegate to the ``execute_*`` functions in
``lgtmaybe.cli``. Imported by ``lgtmaybe.cli`` at import time so the commands
register onto the ``main`` / ``config`` groups defined there.
"""

from __future__ import annotations

import json
import os
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
)
from lgtmaybe.config import store
from lgtmaybe.config.loader import load_config


@main.command()
@click.option(
    "--provider",
    default=None,
    help="LLM provider (openai, anthropic, bedrock, vertex, azure, ollama, openrouter)",
)
@click.option("--model", default=None, help="Model name understood by the chosen provider")
@click.option(
    "--fallback-model",
    default=None,
    help="Model to retry with if the primary model fails",
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
    "azure: https://<resource>.openai.azure.com)",
)
@click.option(
    "--min-severity",
    default=None,
    type=click.Choice(["info", "low", "medium", "high", "critical"]),
    help="Minimum severity to report",
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
    "Raise it so a large multi-file diff isn't truncated; default 16384",
)
@click.option(
    "--base",
    default=None,
    help="Base ref to diff the current branch against "
    "(default: the remote's default branch, else main)",
)
@click.option(
    "--working",
    is_flag=True,
    default=False,
    help="Review uncommitted working-tree changes instead of the branch vs base",
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
    api_key: str | None,
    api_base: str | None,
    min_severity: str | None,
    max_files: int | None,
    max_input_tokens: int | None,
    num_ctx: int | None,
    base: str | None,
    working: bool,
    output_format: str | None,
    as_json: bool,
    context_lines: int | None,
    timeout: int | None,
    temperature: float | None,
    reflect: bool | None,
    config_path: str,
) -> None:
    """Review local git changes and print findings — no GitHub needed."""
    cfg = load_config(
        config_path=Path(config_path),
        user_config_path=store.user_config_path(),
        provider=provider,
        model=model,
        min_severity=min_severity,
        max_files=max_files,
        max_input_tokens=max_input_tokens,
        num_ctx=num_ctx,
        context_lines=context_lines,
        timeout=timeout,
        temperature=temperature,
        reflect=reflect,
    )

    runtime = RuntimeOptions(api_key=api_key, api_base=api_base, fallback_model=fallback_model)
    fmt = output_format or ("json" if as_json else "human")
    execute_local_review(cfg, runtime, base=base, working=working, fmt=fmt)


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
        timeout=inputs["timeout"],
        temperature=inputs["temperature"],
        num_ctx=inputs["num_ctx"],
        max_input_tokens=inputs["max_input_tokens"],
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

    runtime = runtime.with_pr_url(pr_url_from_event(event))
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
