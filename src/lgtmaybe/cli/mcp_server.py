"""`lgtmaybe mcp`: the local review, served to coding agents over MCP (stdio).

A coding agent (Claude Code, Cursor, any Model Context Protocol client) calls
the review as a tool instead of shelling out to ``lgtmaybe review`` and parsing
its output. Two tools, one engine path:

- ``review_changes`` returns the findings in the ``json`` format's shape, plus
  the summary line and whether the run was incomplete;
- ``review_instructions`` returns the ``agent`` format: correction instructions
  the agent can apply as written.

Both run ``run_local_review``, the function behind the ``review`` command, with
config loaded exactly as ``review`` loads it (user config, then
``.lgtmaybe.yml``, then the per-call options); credentials come from the
environment or the server's flags and are never persisted.

The MCP SDK is an optional extra (``lgtmaybe[mcp]``) and is imported only by
``build_server``, so importing this module (and the CLI) never pays for it.
stdout is the transport: the tools return values and never print, and the SDK's
stdio transport points fd 1 at stderr while it serves, so a stray write from a
dependency cannot corrupt the stream either.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar, get_args

import click
from pydantic import Field

from lgtmaybe import cli
from lgtmaybe.cli.render import findings_payload, render_findings, strip_hidden_markers
from lgtmaybe.core.models import ReviewConfig, ReviewFinding
from lgtmaybe.core.version import package_version
from lgtmaybe.engine import INCOMPLETE_MARKER, request_interrupt

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

_T = TypeVar("_T")

#: Which local changes a tool call reviews; the ``review`` command's three modes.
ReviewMode = Literal["branch", "working", "uncommitted"]
#: ``Severity`` / ``ReviewPreset`` values, spelled out so the tool schemas carry
#: a plain enum (a test keeps them in step with the model enums).
SeverityName = Literal["info", "low", "medium", "high", "critical"]
PresetName = Literal["fast", "full"]

INSTALL_HINT = (
    "`lgtmaybe mcp` needs the MCP Python SDK (2.x). Install it with "
    "`pip install 'lgtmaybe[mcp]'` (or `uv tool install 'lgtmaybe[mcp]'`)."
)

_INSTRUCTIONS = (
    "lgtmaybe reviews the local git changes in this repository with an LLM and "
    "returns review findings. Call review_changes for structured findings (path, "
    "line, severity, title, body, suggestion), or review_instructions for the "
    "same review written as correction instructions to apply. mode 'branch' "
    "reviews committed branch changes against the base branch, 'working' adds "
    "uncommitted edits, 'uncommitted' reviews only the uncommitted edits. A "
    "review makes several model calls, so expect it to take a while; call it "
    "once per change set, not after every edit."
)

# The tool arguments as the client sees them: each type carries its description
# into the generated input schema.
_ModeArg = Annotated[
    ReviewMode,
    Field(
        description="branch: committed changes vs the base branch (default); working: "
        "branch commits plus uncommitted edits; uncommitted: only the uncommitted "
        "edits vs HEAD"
    ),
]
_BaseArg = Annotated[
    str | None,
    Field(
        description="Base ref to diff against (default: origin/HEAD, else origin/main, "
        "origin/master, main, master). Ignored in uncommitted mode"
    ),
]
_SeverityArg = Annotated[
    SeverityName | None,
    Field(description="Drop findings below this severity (default: from config)"),
]
_PresetArg = Annotated[
    PresetName | None,
    Field(
        description="fast: four model calls (default); full: one call per review "
        "category, slower and more thorough"
    ),
]


class ReviewToolError(RuntimeError):
    """A tool call that could not produce a review; its message says why."""


class LocalReviewTools:
    """The MCP tools' behaviour, free of the SDK so it tests against fakes.

    ``load_config`` builds the ``ReviewConfig`` for one call from that call's
    overrides (the command closes over its own flags and the config files, and
    re-reads them each call, so an edited ``.lgtmaybe.yml`` applies without a
    restart). Reviews run one at a time: the engine keeps process-wide state
    (the profiler, the interrupt flag), and two concurrent reviews of one
    worktree would only spend the model calls twice.
    """

    def __init__(
        self, load_config: Callable[..., ReviewConfig], runtime: cli.RuntimeOptions
    ) -> None:
        self.load_config = load_config
        self._runtime = runtime
        self._lock = threading.Lock()

    def review_changes(
        self,
        mode: ReviewMode = "branch",
        base: str | None = None,
        min_severity: SeverityName | None = None,
        preset: PresetName | None = None,
    ) -> dict[str, Any]:
        """Review the local changes and return the findings as data."""
        findings, summary = self._review(mode, base, min_severity, preset)
        return {
            "findings": findings_payload(findings),
            "summary": strip_hidden_markers(summary),
            "incomplete": INCOMPLETE_MARKER in summary,
        }

    def review_instructions(
        self,
        mode: ReviewMode = "branch",
        base: str | None = None,
        min_severity: SeverityName | None = None,
        preset: PresetName | None = None,
    ) -> str:
        """Review the local changes and return correction instructions."""
        findings, summary = self._review(mode, base, min_severity, preset)
        return render_findings(
            findings, summary, fmt="agent", rerun_hint="the `review_instructions` tool"
        )

    def _review(
        self,
        mode: str,
        base: str | None,
        min_severity: str | None,
        preset: str | None,
    ) -> tuple[list[ReviewFinding], str]:
        if mode not in get_args(ReviewMode):
            raise ReviewToolError(
                f"unknown mode {mode!r}: expected one of {', '.join(get_args(ReviewMode))}"
            )
        # Only the options the caller set: an unset one must not override the
        # config files, exactly as an absent flag doesn't on `review`.
        overrides = {
            key: value
            for key, value in (("min_severity", min_severity), ("preset", preset))
            if value is not None
        }
        with self._lock:
            try:
                cfg = self.load_config(**overrides)
                return cli.run_local_review(
                    cfg,
                    self._runtime,
                    base=base,
                    working=mode == "working",
                    uncommitted=mode == "uncommitted",
                )
            except click.ClickException as exc:
                raise ReviewToolError(exc.format_message()) from exc
            except Exception as exc:
                raise ReviewToolError(f"review failed: {exc}") from exc


def build_server(tools: LocalReviewTools) -> MCPServer:
    """Register the tools on an ``MCPServer``. Imports the SDK, so call it lazily."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations

    server: MCPServer = MCPServer(
        name="lgtmaybe",
        instructions=_INSTRUCTIONS,
        version=package_version(),
        website_url="https://lgtmaybe.coles.codes/",
        # The SDK configures the root logger; WARNING keeps third-party INFO
        # chatter out of the client's MCP log, as the CLI's stderr has it.
        # lgtmaybe's own logger has its own handler and level, unaffected.
        log_level="WARNING",
    )
    # Read-only: a review reads the worktree and calls the model, nothing more.
    # Open-world: the model it calls is outside this process.
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    def as_tool_error(call: Callable[[], _T]) -> _T:
        """Run one tool call, turning a review failure into the SDK's tool error."""
        try:
            return call()
        except ReviewToolError as exc:
            raise ToolError(str(exc)) from exc

    def review_changes(
        mode: _ModeArg = "branch",
        base: _BaseArg = None,
        min_severity: _SeverityArg = None,
        preset: _PresetArg = None,
    ) -> dict[str, Any]:
        return as_tool_error(lambda: tools.review_changes(mode, base, min_severity, preset))

    def review_instructions(
        mode: _ModeArg = "branch",
        base: _BaseArg = None,
        min_severity: _SeverityArg = None,
        preset: _PresetArg = None,
    ) -> str:
        return as_tool_error(lambda: tools.review_instructions(mode, base, min_severity, preset))

    server.add_tool(
        review_changes,
        title="Review local changes",
        description="Review the local git changes and return the findings as JSON: "
        "`findings` (each with path, line, severity, title, body and an optional "
        "suggestion), the `summary` line, and `incomplete` (true when some review "
        "calls did not finish, so the findings may be partial).",
        annotations=annotations,
    )
    server.add_tool(
        review_instructions,
        title="Review local changes as fix instructions",
        description="Review the local git changes and return the findings as "
        "plain-text correction instructions: for each, the file and line, the "
        "issue, and the suggested fix, ready to apply.",
        annotations=annotations,
    )
    return server


def require_sdk() -> None:
    """Fail with an install hint, not a traceback, when the extra is missing."""
    try:
        import mcp.server.mcpserver  # noqa: F401
    except ImportError as exc:
        raise click.ClickException(INSTALL_HINT) from exc


@contextmanager
def _default_signals() -> Iterator[None]:
    """Let SIGINT/SIGTERM stop the server, as they would any other process.

    The CLI group installs ``graceful_interrupt``, which turns the first signal
    into a review wind-down and keeps the process running. That suits a CI job;
    a server whose client sends SIGTERM to shut it down must simply exit.
    """
    previous: dict[int, Any] = {}
    wanted: dict[str, Any] = {"SIGINT": signal.default_int_handler, "SIGTERM": signal.SIG_DFL}
    try:
        for name, handler in wanted.items():
            sig = getattr(signal, name, None)
            if sig is not None:
                previous[sig] = signal.signal(sig, handler)
    except (ValueError, OSError):
        pass  # Not the main thread: the caller's handlers stay.
    try:
        yield
    finally:
        for sig, handler in previous.items():
            with suppress(ValueError, OSError):
                signal.signal(sig, handler)


def serve(tools: LocalReviewTools) -> None:
    """Serve the tools over stdio until the client disconnects or signals."""
    server = build_server(tools)
    try:
        with _default_signals(), suppress(KeyboardInterrupt):
            server.run("stdio")
    finally:
        # The client is gone. A review still running in a worker thread would
        # spend model calls nobody will read and hold the process open until it
        # finished; the engine's wind-down skips its queued calls instead.
        request_interrupt()
