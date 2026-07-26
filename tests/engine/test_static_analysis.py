"""Static-analysis fusion (F1): the sandboxed tool runner.

Deterministic linters (ruff, bandit, and semgrep with local rules) run over the
already-fetched changed-file TEXTS in a throwaway temp dir — never a checkout,
never executing PR code. Their output is untrusted grounding for the LLM pass,
not findings to post. Contracts:

- default off — no config, no subprocess, no behaviour change;
- a tool missing from PATH is skipped silently (no error, no subprocess);
- subprocesses get a scrubbed environment (no proxy vars to phone home
  through, HOME pinned inside the sandbox) and a timeout;
- semgrep runs ONLY with locally configured rules (``--config auto`` would
  fetch from the network registry);
- a crashing tool / garbage JSON degrades to no hints for that tool;
- severity is mapped onto the shared Severity scale and floored by
  ``static_analysis.min_severity``;
- a PR path that would escape the temp dir (absolute, ``..``) is never written.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import lgtmaybe.engine.static_analysis as static_analysis
from lgtmaybe.core.models import Provider, ReviewConfig, Severity, StaticAnalysisTool
from lgtmaybe.engine.static_analysis import (
    ToolFinding,
    _write_corpus,
    format_hints,
    run_static_analysis,
)

FILES = {"src/app.py": "import os\nx = eval(input())\n"}


def _cfg(**sa_overrides: object) -> ReviewConfig:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    sa = cfg.static_analysis.model_copy(update={"enabled": True, **sa_overrides})
    return cfg.model_copy(update={"static_analysis": sa})


def _ruff_output(root: str) -> str:
    return json.dumps(
        [
            {
                "code": "F401",
                "message": "`os` imported but unused",
                "filename": f"{root}/src/app.py",
                "location": {"row": 1, "column": 1},
            }
        ]
    )


def _mypy_output() -> str:
    """mypy --output json: JSON Lines, one object per diagnostic."""
    return (
        json.dumps(
            {
                "file": "src/app.py",
                "line": 9,
                "column": 16,
                "message": 'Item "None" of "str | None" has no attribute "splitlines"',
                "code": "union-attr",
                "severity": "error",
            }
        )
        + "\n"
        + json.dumps(
            {
                "file": "src/app.py",
                "line": 9,
                "column": 16,
                "message": "Consider using an explicit None check",
                "code": None,
                "severity": "note",
            }
        )
        + "\n"
    )


def _bandit_output() -> str:
    return json.dumps(
        {
            "results": [
                {
                    "filename": "./src/app.py",
                    "line_number": 2,
                    "test_id": "B307",
                    "issue_text": "Use of possibly insecure function eval.",
                    "issue_severity": "MEDIUM",
                }
            ]
        }
    )


class _FakeRun:
    """Stand-in for subprocess.run: records calls, serves canned stdout per tool."""

    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._outputs = outputs or {}

    def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
        tool = Path(str(argv[0])).name
        self.calls.append({"argv": argv, **kwargs})
        return SimpleNamespace(stdout=self._outputs.get(tool, ""), stderr="", returncode=0)


def _patch_tools(monkeypatch, run: _FakeRun, present: set[str]) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None
    )
    monkeypatch.setattr(subprocess, "run", run)


def test_disabled_by_default_runs_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun()
    _patch_tools(monkeypatch, run, present={"ruff", "bandit", "semgrep"})
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")

    assert cfg.static_analysis.enabled is False
    assert run_static_analysis(FILES, cfg) == []
    assert run.calls == []


def test_missing_tool_is_skipped_silently(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun()
    _patch_tools(monkeypatch, run, present=set())  # nothing installed

    assert run_static_analysis(FILES, _cfg()) == []
    assert run.calls == []


def test_static_analysis_temp_directory_ignores_cleanup_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}
    temporary_directory = static_analysis.tempfile.TemporaryDirectory

    def recording_temp_directory(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return temporary_directory(*args, **kwargs)

    monkeypatch.setattr(static_analysis.tempfile, "TemporaryDirectory", recording_temp_directory)
    _patch_tools(monkeypatch, _FakeRun(), present=set())

    run_static_analysis(FILES, _cfg())

    assert captured["ignore_cleanup_errors"] is True


def test_corpus_written_utf8(tmp_path: Path) -> None:
    source = "print('你好, мир, 👍')\n"

    assert _write_corpus(tmp_path, {"src/app.py": source}) == ["src/app.py"]
    assert (tmp_path / "src" / "app.py").read_bytes().decode("utf-8").splitlines() == [
        source.rstrip()
    ]


def test_relativise_returns_forward_slash_paths(tmp_path: Path) -> None:
    absolute = tmp_path / "src" / "app.py"

    assert static_analysis._posix_rel(str(absolute), tmp_path) == "src/app.py"
    assert static_analysis._posix_rel(r"src\app.py", tmp_path) == "src/app.py"
    assert static_analysis._posix_rel(r".\src\app.py") == "src/app.py"
    assert static_analysis._posix_rel("./src/app.py") == "src/app.py"


def test_ruff_findings_parsed_and_relativised(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured_root: dict[str, str] = {}

    class _Run(_FakeRun):
        def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
            captured_root["cwd"] = str(kwargs["cwd"])
            self.calls.append({"argv": argv, **kwargs})
            return SimpleNamespace(
                stdout=_ruff_output(captured_root["cwd"]), stderr="", returncode=0
            )

    run = _Run()
    _patch_tools(monkeypatch, run, present={"ruff"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.ruff]))

    assert findings == [
        ToolFinding(
            tool="ruff",
            path="src/app.py",
            line=1,
            rule="F401",
            message="`os` imported but unused",
            severity=Severity.low,
        )
    ]


def test_bandit_findings_parsed_with_severity_mapping(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun(outputs={"bandit": _bandit_output()})
    _patch_tools(monkeypatch, run, present={"bandit"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.bandit]))

    assert len(findings) == 1
    assert findings[0].tool == "bandit"
    assert findings[0].path == "src/app.py"
    assert findings[0].line == 2
    assert findings[0].rule == "B307"
    assert findings[0].severity is Severity.medium


def test_mypy_findings_parsed_from_json_lines(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """mypy emits JSON Lines — one object per line, not an array like ruff.

    Parsed with json.loads over the whole payload it raises, and the tool would
    degrade to silence with only a warning in the log.
    """
    run = _FakeRun(outputs={"mypy": _mypy_output()})
    _patch_tools(monkeypatch, run, present={"mypy"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.mypy]))

    assert [(f.rule, f.line, f.severity) for f in findings] == [
        ("union-attr", 9, Severity.medium),
        ("note", 9, Severity.info),
    ]
    assert findings[0].path == "src/app.py"
    assert findings[0].tool == "mypy"


def test_mypy_blank_lines_are_skipped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A clean run prints nothing; a trailing newline must not parse as garbage
    and take the whole tool's findings down with it."""
    run = _FakeRun(outputs={"mypy": "\n\n"})
    _patch_tools(monkeypatch, run, present={"mypy"})

    assert run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.mypy])) == []


def test_mypy_runs_isolated_from_the_wider_codebase(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The corpus is only the CHANGED files, so mypy must not treat every
    unresolvable import as an error — that noise would bury the real hits and
    blow the MAX_HINTS cap on any normal PR."""
    run = _FakeRun(outputs={"mypy": ""})
    _patch_tools(monkeypatch, run, present={"mypy"})

    run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.mypy]))

    argv = [str(a) for a in run.calls[0]["argv"]]  # type: ignore[union-attr]
    assert "--ignore-missing-imports" in argv
    assert "--follow-imports=skip" in argv
    # Never reuse or write a cache keyed to another run's corpus.
    assert "--no-incremental" in argv


@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed")
def test_mypy_really_catches_an_unguarded_none_deref(tmp_path: Path) -> None:
    """The regression this tool was added for, run against the real binary.

    A faked subprocess proves we parse mypy's output; it cannot prove the flags
    we pass still surface the finding. This is the bug a live review missed and
    mypy caught: `dict.get()` narrows to `str | None`, and `.splitlines()` on it
    crashes at runtime.
    """
    files = {
        "src/metrics.py": (
            "def summarise(findings: list, file_contents: dict[str, str]) -> int:\n"
            "    total = 0\n"
            "    for finding in findings:\n"
            "        text = file_contents.get(finding.path)\n"
            "        total += len(text.splitlines())\n"
            "    return total\n"
        )
    }

    findings = run_static_analysis(files, _cfg(tools=[StaticAnalysisTool.mypy]))

    assert any(f.rule == "union-attr" and f.line == 5 for f in findings), findings


def test_min_severity_floor_drops_weak_hints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun(outputs={"bandit": _bandit_output()})
    _patch_tools(monkeypatch, run, present={"ruff", "bandit"})

    findings = run_static_analysis(FILES, _cfg(min_severity=Severity.high))

    assert findings == []  # bandit MEDIUM and ruff (low) both below the floor


def test_subprocess_env_is_scrubbed_of_proxies_and_home(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sekret")
    run = _FakeRun(outputs={"bandit": _bandit_output()})
    _patch_tools(monkeypatch, run, present={"bandit"})

    run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.bandit]))

    env = run.calls[0]["env"]
    assert isinstance(env, dict)
    assert "HTTPS_PROXY" not in env and "HTTP_PROXY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    # HOME pinned inside the sandbox so tools can't read user-level config.
    assert env["HOME"] == str(run.calls[0]["cwd"])
    # And every call carries a timeout — a hung linter can't hang the review.
    assert run.calls[0]["timeout"]


def test_scrubbed_env_windows_passes_system_vars(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(static_analysis, "_WINDOWS", True, raising=False)
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT")
    monkeypatch.setenv("TEMP", r"C:\Temp")
    monkeypatch.setenv("TMP", r"C:\Tmp")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "must-not-leak")

    env = static_analysis._scrubbed_env(tmp_path)

    assert env["SystemRoot"] == r"C:\Windows"
    assert env["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert env["PATHEXT"] == ".COM;.EXE;.BAT"
    assert env["TEMP"] == r"C:\Temp"
    assert env["TMP"] == r"C:\Tmp"
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        assert env[key] == str(tmp_path)
    assert "AWS_ACCESS_KEY_ID" not in env


def test_scrubbed_env_posix_stays_minimal(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(static_analysis, "_WINDOWS", False, raising=False)
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "must-not-leak")

    assert static_analysis._scrubbed_env(tmp_path) == {
        "PATH": static_analysis.os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "NO_COLOR": "1",
        "SEMGREP_SEND_METRICS": "off",
    }


def test_semgrep_skipped_without_local_rules(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """semgrep with no --config would need the network registry — never run it
    unless the user configured local rules."""
    run = _FakeRun()
    _patch_tools(monkeypatch, run, present={"semgrep"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.semgrep]))

    assert findings == []
    assert run.calls == []


def test_semgrep_runs_offline_with_local_rules(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    output = json.dumps(
        {
            "results": [
                {
                    "path": "src/app.py",
                    "start": {"line": 2},
                    "check_id": "python.lang.security.eval",
                    "extra": {"message": "eval is dangerous", "severity": "ERROR"},
                }
            ]
        }
    )
    run = _FakeRun(outputs={"semgrep": output})
    _patch_tools(monkeypatch, run, present={"semgrep"})

    findings = run_static_analysis(
        FILES, _cfg(tools=[StaticAnalysisTool.semgrep], semgrep_rules=str(rules))
    )

    assert findings[0].rule == "python.lang.security.eval"
    assert findings[0].severity is Severity.high
    argv = [str(a) for a in run.calls[0]["argv"]]
    assert "--metrics=off" in argv
    assert "--disable-version-check" in argv


def test_crashing_tool_degrades_to_no_hints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(argv: list[str], **kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", boom)

    assert run_static_analysis(FILES, _cfg()) == []


def test_garbage_json_degrades_to_no_hints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun(outputs={"ruff": "not json", "bandit": "{]"})
    _patch_tools(monkeypatch, run, present={"ruff", "bandit"})

    assert run_static_analysis(FILES, _cfg()) == []


def test_escaping_paths_are_never_written(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A malicious PR path must not be written outside the sandbox dir."""
    run = _FakeRun()
    _patch_tools(monkeypatch, run, present=set())
    sentinel = tmp_path / "evil.py"
    absolute_sentinel = tmp_path / "absolute-evil.py"
    evil = {"../evil.py": "x = 1", str(absolute_sentinel): "x = 1"}

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    run_static_analysis(evil, _cfg())

    assert not sentinel.exists()
    assert not absolute_sentinel.exists()


def test_format_hints_renders_tool_rule_path_line() -> None:
    finding = ToolFinding(
        tool="bandit",
        path="src/app.py",
        line=2,
        rule="B307",
        message="Use of possibly insecure function eval.",
        severity=Severity.medium,
    )

    text = format_hints([finding])

    assert "bandit" in text
    assert "B307" in text
    assert "src/app.py:2" in text
    assert "eval" in text


def test_per_tool_severity_floor_overrides_the_global_floor(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """tool_min_severity floors one tool without touching the others: ruff's
    low-grade hits are dropped while bandit's medium hit survives."""
    run = _FakeRun(outputs={"ruff": _ruff_output("/r"), "bandit": _bandit_output()})

    class _Run(_FakeRun):
        def __call__(self, argv, **kwargs):  # type: ignore[no-untyped-def]
            tool = Path(str(argv[0])).name
            self.calls.append({"argv": argv, **kwargs})
            out = _ruff_output(str(kwargs["cwd"])) if tool == "ruff" else _bandit_output()
            return SimpleNamespace(stdout=out, stderr="", returncode=0)

    run = _Run()
    _patch_tools(monkeypatch, run, present={"ruff", "bandit"})

    findings = run_static_analysis(
        FILES,
        _cfg(tool_min_severity={StaticAnalysisTool.ruff: Severity.medium}),
    )

    assert [f.tool for f in findings] == ["bandit"]


def test_per_tool_floor_defaults_to_the_global_floor(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun(outputs={"bandit": _bandit_output()})
    _patch_tools(monkeypatch, run, present={"bandit"})

    findings = run_static_analysis(
        FILES,
        _cfg(
            min_severity=Severity.high,  # global floor drops bandit's medium…
            tool_min_severity={StaticAnalysisTool.bandit: Severity.low},  # …but its own floor wins
        ),
    )

    assert [f.tool for f in findings] == ["bandit"]


def test_tools_run_concurrently(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Each tool is an independent subprocess with its own 180s cap, so running
    them one after another stacks their worst cases. Nothing is shared but the
    read-only corpus, so they overlap."""
    import threading

    lock = threading.Lock()
    events: list[str] = []

    def slow_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        tool = Path(argv[0]).name
        with lock:
            events.append(f"start-{tool}")
        threading.Event().wait(0.05)
        with lock:
            events.append(f"end-{tool}")
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(static_analysis.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", slow_run)

    cfg = _cfg(enabled=True, tools=list(StaticAnalysisTool))
    static_analysis.run_static_analysis({"a.py": "x = 1\n"}, cfg)

    first_end = next(i for i, e in enumerate(events) if e.startswith("end-"))
    starts_before = [e for e in events[:first_end] if e.startswith("start-")]
    assert len(starts_before) >= 2, f"tools ran serially: {events}"


def test_concurrent_tools_keep_deterministic_finding_order(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Whichever subprocess finishes first, the hint list must be ordered by the
    configured tool list — hints feed the prompt, and a prompt that reorders run
    to run defeats the shared-prefix cache."""
    payloads = {
        "ruff": json.dumps(
            [{"filename": "a.py", "location": {"row": 1}, "code": "F401", "message": "ruff msg"}]
        ),
        "bandit": json.dumps(
            {
                "results": [
                    {
                        "filename": "a.py",
                        "line_number": 1,
                        "test_id": "B101",
                        "issue_text": "bandit msg",
                        "issue_severity": "HIGH",
                    }
                ]
            }
        ),
    }

    import threading

    def run(argv, **kwargs):  # type: ignore[no-untyped-def]
        tool = Path(argv[0]).name
        # ruff is configured first but deliberately made the slower one.
        threading.Event().wait(0.05 if tool == "ruff" else 0.0)
        return subprocess.CompletedProcess(argv, 0, stdout=payloads.get(tool, "[]"), stderr="")

    monkeypatch.setattr(static_analysis.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", run)

    cfg = _cfg(enabled=True, tools=[StaticAnalysisTool.ruff, StaticAnalysisTool.bandit])
    findings = static_analysis.run_static_analysis({"a.py": "x = 1\n"}, cfg)

    assert [f.tool for f in findings] == ["ruff", "bandit"]


def test_enabled_with_no_tools_returns_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Enabling the feature but selecting no tools must degrade to no hints, not
    raise — every other "nothing to do" path here returns [] quietly."""
    run = _FakeRun()
    _patch_tools(monkeypatch, run, present={"ruff", "bandit", "semgrep"})

    cfg = _cfg(enabled=True, tools=[])
    findings = static_analysis.run_static_analysis({"a.py": "x = 1\n"}, cfg)

    assert findings == []
    assert run.calls == []
