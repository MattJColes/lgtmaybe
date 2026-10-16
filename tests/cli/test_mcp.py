"""`lgtmaybe mcp`: the local review served to coding agents over MCP (stdio).

The tool functions are tested against the fake provider/engine (no network, no
model); the SDK-backed tests run the real server in-process and as a stdio
subprocess, so a stray write to stdout (the transport) or a signal handler that
swallows SIGTERM shows up here rather than as a hung or garbled agent session.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
from click.testing import CliRunner

import lgtmaybe.cli as cli_module
from lgtmaybe.cli import RuntimeOptions, main
from lgtmaybe.cli.mcp_server import (
    LocalReviewTools,
    PresetName,
    ReviewToolError,
    SeverityName,
)
from lgtmaybe.core.models import PRContext, ReviewConfig, ReviewFinding, ReviewPreset, Severity
from lgtmaybe.engine import INCOMPLETE_MARKER
from tests.fakes import FakeEngine, FakeProvider

_CTX = PRContext(
    diff="@@ -1 +1 @@\n-a\n+b\n",
    changed_files=["a.py"],
    base_sha="base",
    head_sha="head",
    repo="org/repo",
    pr_number=0,
)


def _cfg(**overrides: Any) -> ReviewConfig:
    base: dict[str, Any] = {"provider": "ollama", "model": "llama3"}
    base.update({k: v for k, v in overrides.items() if v is not None})
    return ReviewConfig.model_validate(base)


def _patch_local(monkeypatch, engine: Any = None) -> list[dict[str, Any]]:
    """Wire the shared local-review path onto fakes; return the git-context calls."""
    calls: list[dict[str, Any]] = []
    engine = engine if engine is not None else FakeEngine(FakeProvider())
    monkeypatch.setattr(cli_module, "build_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(cli_module, "LLMReviewEngine", lambda provider, **kwargs: engine)

    def _context(**kwargs: Any) -> PRContext:
        calls.append(kwargs)
        return _CTX

    monkeypatch.setattr(cli_module, "local_pr_context", _context)
    return calls


def _tools(loaded: list[dict[str, Any]] | None = None) -> LocalReviewTools:
    def load(**overrides: Any) -> ReviewConfig:
        if loaded is not None:
            loaded.append(overrides)
        return _cfg(**overrides)

    return LocalReviewTools(load, RuntimeOptions())


class _IncompleteEngine(FakeEngine):
    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        findings, _ = super().review(ctx, cfg)
        return findings, f"1 finding · results may be incomplete\n{INCOMPLETE_MARKER}"


class TestSharedLocalReview:
    def test_run_local_review_returns_the_engine_result(self, monkeypatch) -> None:
        """The function `review` and the MCP tools share returns, not prints."""
        _patch_local(monkeypatch)

        findings, summary = cli_module.run_local_review(
            _cfg(), RuntimeOptions(), base=None, working=False
        )

        assert [f.title for f in findings] == ["canned finding"]
        assert "model llama3" in summary


class TestReviewChangesTool:
    @pytest.mark.parametrize(
        ("mode", "working", "uncommitted"),
        [("branch", False, False), ("working", True, False), ("uncommitted", False, True)],
    )
    def test_mode_selects_the_diff(self, monkeypatch, mode, working, uncommitted) -> None:
        calls = _patch_local(monkeypatch)

        _tools().review_changes(mode=mode, base="origin/dev")

        assert calls == [{"base": "origin/dev", "working": working, "uncommitted": uncommitted}]

    def test_returns_findings_in_the_json_format_plus_summary(self, monkeypatch) -> None:
        _patch_local(monkeypatch)

        result = _tools().review_changes()

        expected = [
            f.model_dump(mode="json") for f in FakeEngine(FakeProvider()).review(_CTX, _cfg())[0]
        ]
        assert result["findings"] == expected
        assert result["summary"] == "1 findings · model llama3"
        assert result["incomplete"] is False

    def test_incomplete_run_is_flagged_and_the_marker_stripped(self, monkeypatch) -> None:
        _patch_local(monkeypatch, engine=_IncompleteEngine(FakeProvider()))

        result = _tools().review_changes()

        assert result["incomplete"] is True
        assert "<!--" not in result["summary"]
        assert "results may be incomplete" in result["summary"]

    def test_per_call_options_reach_the_config_loader(self, monkeypatch) -> None:
        """min_severity / preset ride the same loader as `review`'s flags."""
        _patch_local(monkeypatch)
        loaded: list[dict[str, Any]] = []

        result = _tools(loaded).review_changes(min_severity="high", preset="full")

        assert loaded == [{"min_severity": "high", "preset": "full"}]
        # The canned finding is low severity, so the high floor drops it.
        assert result["findings"] == []

    def test_unset_options_are_not_passed_so_config_wins(self, monkeypatch) -> None:
        _patch_local(monkeypatch)
        loaded: list[dict[str, Any]] = []

        _tools(loaded).review_changes()

        assert loaded == [{}]

    def test_unknown_mode_is_a_tool_error(self, monkeypatch) -> None:
        _patch_local(monkeypatch)

        with pytest.raises(ReviewToolError, match="mode"):
            _tools().review_changes(mode="everything")  # type: ignore[arg-type]

    def test_failure_is_raised_with_its_reason_never_an_empty_result(self, monkeypatch) -> None:
        _patch_local(monkeypatch)

        def boom(**_kwargs: Any) -> PRContext:
            raise RuntimeError("not a git repository")

        monkeypatch.setattr(cli_module, "local_pr_context", boom)

        with pytest.raises(ReviewToolError, match="not a git repository"):
            _tools().review_changes()

    def test_git_timeout_keeps_its_own_message(self, monkeypatch) -> None:
        """A hung git surfaces with its own message, not the generic
        "review failed" prefix: run_local_review converts the local module's
        error at the cli seam before this layer sees it."""
        from lgtmaybe.local import LocalGitError

        _patch_local(monkeypatch)

        def hung_git(**_kwargs: Any) -> PRContext:
            raise LocalGitError("git diff timed out after 120s")

        monkeypatch.setattr(cli_module, "local_pr_context", hung_git)

        with pytest.raises(ReviewToolError, match=r"^git diff timed out after 120s"):
            _tools().review_changes()

    def test_config_error_is_a_tool_error(self, monkeypatch) -> None:
        _patch_local(monkeypatch)

        def bad_config(**_overrides: Any) -> ReviewConfig:
            raise ValueError("config file .lgtmaybe.yml is not a mapping")

        tools = LocalReviewTools(bad_config, RuntimeOptions())
        with pytest.raises(ReviewToolError, match="not a mapping"):
            tools.review_changes()

    def test_tool_writes_nothing_to_stdout(self, monkeypatch, capsys) -> None:
        """stdout is the MCP transport: the tool returns, it never echoes."""
        _patch_local(monkeypatch)

        _tools().review_changes()
        _tools().review_instructions()

        assert capsys.readouterr().out == ""


class TestReviewInstructionsTool:
    def test_returns_the_agent_format(self, monkeypatch) -> None:
        _patch_local(monkeypatch)

        text = _tools().review_instructions(mode="working")

        assert "apply each correction below" in text
        assert "a.py:1" in text and "canned finding" in text

    def test_tells_the_agent_to_rerun_the_tool_not_the_cli(self, monkeypatch) -> None:
        _patch_local(monkeypatch)

        text = _tools().review_instructions()

        assert "review_instructions" in text
        assert "lgtmaybe review" not in text


def test_literal_choices_track_the_enums() -> None:
    """The tool schemas enumerate severities/presets; a new member can't be missed."""
    assert list(get_args(SeverityName)) == [s.value for s in Severity]
    assert list(get_args(PresetName)) == [p.value for p in ReviewPreset]


class TestMcpCommand:
    def test_listed_in_help_and_examples(self) -> None:
        result = CliRunner().invoke(main, ["--help"])
        assert "mcp" in result.output
        assert "lgtmaybe mcp" in result.output.split("Examples:", 1)[1]

    def test_missing_sdk_fails_cleanly_naming_the_extra(self, monkeypatch) -> None:
        for name in ("mcp", "mcp.server", "mcp.server.mcpserver"):
            monkeypatch.setitem(sys.modules, name, None)

        result = CliRunner().invoke(main, ["mcp", "--provider", "ollama"])

        assert result.exit_code == 1
        assert "lgtmaybe[mcp]" in result.output
        assert "Traceback" not in result.output

    def test_bad_config_fails_before_serving(self, monkeypatch, tmp_path) -> None:
        pytest.importorskip("mcp.server.mcpserver")
        import lgtmaybe.cli.mcp_server as mcp_server

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mcp_server, "serve", lambda tools: pytest.fail("served"))
        (tmp_path / "broken.yml").write_text("- not a mapping\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["mcp", "--config", "broken.yml"])

        assert result.exit_code == 1, result.output

    def test_serves_with_the_command_level_options(self, monkeypatch, tmp_path) -> None:
        pytest.importorskip("mcp.server.mcpserver")
        import lgtmaybe.cli.mcp_server as mcp_server

        # Record the current cwd, so monkeypatch undoes the chdir that -C makes.
        monkeypatch.chdir(Path.cwd())
        served: list[LocalReviewTools] = []
        monkeypatch.setattr(mcp_server, "serve", served.append)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        result = CliRunner().invoke(
            main,
            [
                "mcp",
                "--provider",
                "ollama",
                "--model",
                "qwen3:27b",
                "--timeout",
                "900",
                "-C",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0, result.output
        assert result.output == ""  # nothing on stdout before the transport starts
        assert Path.cwd().samefile(tmp_path)
        cfg = served[0].load_config()
        assert (cfg.provider.value, cfg.model, cfg.timeout) == ("ollama", "qwen3:27b", 900)


class TestServeLifecycle:
    @pytest.fixture(autouse=True)
    def _clear(self):  # type: ignore[no-untyped-def]
        from lgtmaybe.engine import clear_interrupt

        clear_interrupt()
        yield
        clear_interrupt()

    def test_serves_with_default_signal_handling_then_winds_down(self, monkeypatch) -> None:
        """While serving, SIGTERM is the default (the process stops); once the
        client is gone, a review still in flight is told to wind down."""
        import lgtmaybe.cli.mcp_server as mcp_server
        from lgtmaybe.engine import interrupt_requested

        seen: dict[str, Any] = {}

        class _FakeServer:
            def run(self, transport: str) -> None:
                seen["transport"] = transport
                seen["sigterm"] = signal.getsignal(signal.SIGTERM)
                seen["interrupted_while_serving"] = interrupt_requested()
                raise KeyboardInterrupt

        monkeypatch.setattr(mcp_server, "build_server", lambda tools: _FakeServer())
        before = signal.getsignal(signal.SIGTERM)

        mcp_server.serve(_tools())

        assert seen == {
            "transport": "stdio",
            "sigterm": signal.SIG_DFL,
            "interrupted_while_serving": False,
        }
        assert interrupt_requested()
        assert signal.getsignal(signal.SIGTERM) is before


# --------------------------------------------------------------------------- SDK


def _run(coro_fn: Any) -> Any:
    import anyio

    return anyio.run(coro_fn)


class TestInProcessServer:
    @pytest.fixture(autouse=True)
    def _sdk(self) -> None:
        pytest.importorskip("mcp.server.mcpserver")

    def test_lists_both_tools_as_read_only(self, monkeypatch) -> None:
        from mcp import Client

        from lgtmaybe.cli.mcp_server import build_server

        server = build_server(_tools())

        async def go() -> Any:
            async with Client(server) as client:
                return await client.list_tools()

        tools = {t.name: t for t in _run(go).tools}
        assert set(tools) == {"review_changes", "review_instructions"}
        for tool in tools.values():
            assert tool.annotations is not None and tool.annotations.read_only_hint is True
            assert tool.input_schema["properties"]["mode"]["enum"] == [
                "branch",
                "working",
                "uncommitted",
            ]

    def test_review_changes_round_trip(self, monkeypatch) -> None:
        from mcp import Client

        from lgtmaybe.cli.mcp_server import build_server

        _patch_local(monkeypatch)
        server = build_server(_tools())

        async def go() -> Any:
            async with Client(server) as client:
                return await client.call_tool("review_changes", {"mode": "working"})

        result = _run(go)
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["findings"][0]["title"] == "canned finding"
        text = json.loads(result.content[0].text)
        assert text["summary"] == "1 findings · model llama3"

    def test_review_instructions_round_trip(self, monkeypatch) -> None:
        from mcp import Client

        from lgtmaybe.cli.mcp_server import build_server

        _patch_local(monkeypatch)
        server = build_server(_tools())

        async def go() -> Any:
            async with Client(server) as client:
                return await client.call_tool("review_instructions", {})

        result = _run(go)
        assert result.is_error is False
        assert "canned finding" in result.content[0].text

    @pytest.mark.parametrize("tool", ["review_changes", "review_instructions"])
    def test_failure_is_a_tool_error_and_the_server_lives_on(self, monkeypatch, tool) -> None:
        from mcp import Client

        from lgtmaybe.cli.mcp_server import build_server

        _patch_local(monkeypatch)

        def boom(**_kwargs: Any) -> PRContext:
            raise RuntimeError("not a git repository")

        monkeypatch.setattr(cli_module, "local_pr_context", boom)
        server = build_server(_tools())

        async def go() -> Any:
            async with Client(server) as client:
                failed = await client.call_tool(tool, {})
                listed = await client.list_tools()
                return failed, listed

        failed, listed = _run(go)
        assert failed.is_error is True
        assert "not a git repository" in failed.content[0].text
        assert len(listed.tools) == 2


# ------------------------------------------------------------------ stdio process

_HANDSHAKE = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


def _spawn(tmp_path: Path) -> subprocess.Popen[bytes]:
    """Start the server outside any repository; its stderr goes to a file.

    A file rather than a pipe: nothing reads stderr until the end, and a pipe
    nobody drains can fill and stall the server.
    """
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["APPDATA"] = str(tmp_path / "appdata")
    env.pop("LGTMAYBE_API_KEY", None)
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    with (tmp_path / "stderr.log").open("wb") as stderr:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "lgtmaybe",
                "mcp",
                "--provider",
                "openai",
                "--model",
                "gpt-4o",
                "--api-key",
                "sk-test-not-real",
                "-C",
                str(repo),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=env,
        )


def _send(proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


_EOF = b""


class _LineReader:
    """Read a pipe's lines on a daemon thread, so every read has a timeout.

    A bare ``readline()`` blocks until the server writes, so a hung server
    would hang CI; here it fails the test instead.
    """

    def __init__(self, stream: Any) -> None:
        self._lines: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, args=(stream,), daemon=True).start()

    def _pump(self, stream: Any) -> None:
        for line in iter(stream.readline, _EOF):
            self._lines.put(line)
        self._lines.put(_EOF)

    def next(self, timeout: float) -> bytes:
        """The next line, or ``_EOF`` at end of stream; fails the test on timeout."""
        try:
            return self._lines.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            pytest.fail(f"the MCP server wrote nothing for {timeout:.0f}s")

    def until_eof(self, timeout: float) -> list[bytes]:
        """Every remaining line, up to end of stream."""
        deadline = time.monotonic() + timeout
        lines: list[bytes] = []
        while (line := self.next(deadline - time.monotonic())) != _EOF:
            lines.append(line)
        return lines


@pytest.fixture
def server_proc(tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    """The spawned server, killed at teardown if a failed test left it running."""
    proc = _spawn(tmp_path)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)


class TestStdioProcess:
    @pytest.fixture(autouse=True)
    def _sdk(self) -> None:
        pytest.importorskip("mcp.server.mcpserver")

    def test_stdout_carries_only_json_rpc_and_errors_are_tool_errors(
        self, server_proc, tmp_path
    ) -> None:
        """A failing review (no git repo) comes back as an MCP tool error, and
        every byte the process writes to stdout is a JSON-RPC message."""
        proc = server_proc
        assert proc.stdout is not None and proc.stdin is not None
        reader = _LineReader(proc.stdout)
        for message in _HANDSHAKE:
            _send(proc, message)
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "review_changes", "arguments": {"mode": "branch"}},
            },
        )
        # Collect replies until the call answers, then close stdin, which ends
        # the server, and collect whatever it wrote on the way out.
        lines: list[bytes] = []
        deadline = time.monotonic() + 120
        while (line := reader.next(deadline - time.monotonic())) != _EOF:
            lines.append(line)
            if b'"id":3' in line.replace(b" ", b""):
                break
        proc.stdin.close()
        lines.extend(reader.until_eof(timeout=60))
        proc.wait(timeout=60)
        stderr = (tmp_path / "stderr.log").read_bytes()

        lines = [line for line in lines if line.strip()]
        messages = [json.loads(line) for line in lines]  # any non-JSON line fails here
        assert all(m.get("jsonrpc") == "2.0" for m in messages), lines
        by_id = {m.get("id"): m for m in messages}
        names = {t["name"] for t in by_id[2]["result"]["tools"]}
        assert names == {"review_changes", "review_instructions"}
        call = by_id[3]["result"]
        assert call["isError"] is True
        assert "git" in call["content"][0]["text"].lower()
        assert b"sk-test-not-real" not in stderr

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
    def test_sigterm_stops_the_server(self, server_proc) -> None:
        """The CLI's graceful-interrupt handler turns the first SIGTERM into a
        review wind-down; a server must stop on it instead, or the agent that
        launched it cannot shut it down."""
        proc = server_proc
        assert proc.stdout is not None
        reader = _LineReader(proc.stdout)
        _send(proc, _HANDSHAKE[0])
        assert json.loads(reader.next(timeout=60))["id"] == 1

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pytest.fail("the MCP server ignored SIGTERM")


def test_the_preset_argument_states_the_fast_call_count() -> None:
    """An MCP client reads this description to choose a preset, so it must match the engine.

    `fast` runs five calls (security, correctness, code health, artefacts,
    architecture), plus one more when a committed spec matches.
    """
    import typing

    from lgtmaybe.cli.mcp_server import _PresetArg

    (field,) = typing.get_args(_PresetArg)[1:]
    assert "fast: five model calls" in field.description
    assert "four" not in field.description
