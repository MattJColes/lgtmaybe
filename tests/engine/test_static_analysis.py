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

from lgtmaybe.core.models import Provider, ReviewConfig, Severity, StaticAnalysisTool
from lgtmaybe.engine.static_analysis import ToolFinding, format_hints, run_static_analysis

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
    evil = {"../evil.py": "x = 1", "/abs/evil.py": "x = 1"}
    sentinel = tmp_path / "evil.py"

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    run_static_analysis(evil, _cfg())

    assert not sentinel.exists()
    assert not Path("/abs/evil.py").exists()


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
