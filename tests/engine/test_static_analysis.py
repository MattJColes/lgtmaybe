"""Static-analysis fusion (F1): the sandboxed tool runner.

Deterministic tools (ruff, bandit, mypy, gitleaks, and semgrep with local rules)
run over the already-fetched changed-file TEXTS in a throwaway temp dir — never a
checkout, never executing PR code. Their output is untrusted: it either grounds
the LLM pass or, for a `finding`-mode tool, is redacted and mapped onto findings
(see test_tool_findings.py). Contracts:

- default off — no config, no subprocess, no behaviour change;
- a tool missing from PATH is skipped silently (no error, no subprocess);
- subprocesses get a scrubbed environment (no proxy vars to phone home
  through, HOME pinned inside the sandbox) and a timeout;
- semgrep always runs against local rules — the bundled MIT pack by default —
  never ``--config auto``, which would fetch from the network registry;
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


def test_dotted_key_paths_tolerate_a_missing_level() -> None:
    """`_get` walks a tool's nesting; a missing or non-dict level yields None.

    The table-driven mappers read every field through this, so a tool that omits
    an optional wrapper (semgrep's ``extra``, ast-grep's ``range``) must degrade
    to the spec's default rather than raising and losing the whole tool's output.
    """
    item = {"start": {"line": 4}, "extra": {"severity": "ERROR"}, "check_id": None}

    assert static_analysis._get(item, "start.line") == 4
    assert static_analysis._get(item, "extra.severity") == "ERROR"
    assert static_analysis._get(item, "missing.line") is None
    assert static_analysis._get(item, "start.line.deeper") is None  # int is not a dict
    assert static_analysis._get(item, "check_id") is None
    # An empty path reads nothing — how an ungraded tool falls to its default.
    assert static_analysis._get(item, "") is None


def test_every_tool_is_either_table_driven_or_hand_written() -> None:
    """The `_SPECS` table and the hand-written mappers must partition the tools.

    `_parse_output` sends anything that is not osv-scanner or zizmor through the
    table, so a tool added to the enum without a `_Spec` would raise a KeyError
    at parse time and silently lose that tool's findings.
    """
    hand_written = {StaticAnalysisTool.osv_scanner, StaticAnalysisTool.zizmor}

    assert set(static_analysis._SPECS) | hand_written == set(StaticAnalysisTool)
    assert set(static_analysis._SPECS) & hand_written == set()
    # The finding's tool label is the enum value, so the two cannot drift apart.
    assert all(t.value for t in static_analysis._SPECS)


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
    monkeypatch.delenv("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY", raising=False)

    assert static_analysis._scrubbed_env(tmp_path) == {
        "PATH": static_analysis.os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "NO_COLOR": "1",
        "SEMGREP_SEND_METRICS": "off",
    }


def test_scrubbed_env_passes_through_the_offline_vulnerability_database(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    """The one path variable the sandbox forwards, and why it is safe to.

    osv-scanner cannot fetch a database from inside a network-less sandbox, so
    the image bakes one in and points at it with this variable. It names a
    read-only local directory — no credential, no endpoint — and without it the
    scanner silently finds nothing, which reads like a clean bill of health.
    """
    monkeypatch.setattr(static_analysis, "_WINDOWS", False, raising=False)
    monkeypatch.setenv("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY", "/opt/osv-db")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")

    env = static_analysis._scrubbed_env(tmp_path)

    assert env["OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY"] == "/opt/osv-db"
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_semgrep_never_uses_the_network_registry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """semgrep must always run against local rules, never `--config auto`.

    It used to satisfy this by refusing to run at all without configured rules,
    which meant it never ran. It now falls back to the bundled MIT pack, so the
    contract to protect is the narrower one: the rules are always local.
    """
    run = _FakeRun(outputs={"semgrep": json.dumps({"results": []})})
    _patch_tools(monkeypatch, run, present={"semgrep"})

    run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.semgrep]))

    argv = [str(a) for a in run.calls[0]["argv"]]
    assert "auto" not in argv
    config = argv[argv.index("--config") + 1]
    assert Path(config).is_dir() or Path(config).is_file(), "rules must be a local path"


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


class _FakeReportRun(_FakeRun):
    """A tool that writes its findings to --report-path instead of stdout."""

    def __init__(self, report: str) -> None:
        super().__init__()
        self._report = report

    def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
        args = [str(a) for a in argv]
        if "--report-path" in args:
            Path(args[args.index("--report-path") + 1]).write_text(self._report, encoding="utf-8")
        return super().__call__(argv, **kwargs)


def _gitleaks_report() -> str:
    """gitleaks JSON. `Match`/`Secret` carry the credential — we must ignore them."""
    return json.dumps(
        [
            {
                "RuleID": "aws-access-key-id",
                "Description": "AWS Access Key",
                "File": "src/app.py",
                "StartLine": 2,
                "Match": "AKIAIOSFODNN7EXAMPLE",
                "Secret": "AKIAIOSFODNN7EXAMPLE",
                "Line": "KEY = 'AKIAIOSFODNN7EXAMPLE'",
            }
        ]
    )


def test_gitleaks_findings_are_read_from_its_report_file(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeReportRun(_gitleaks_report())
    _patch_tools(monkeypatch, run, present={"gitleaks"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.gitleaks]))

    assert [(f.tool, f.path, f.line, f.rule) for f in findings] == [
        ("gitleaks", "src/app.py", 2, "aws-access-key-id")
    ]
    # A committed credential is high by definition — gitleaks has no severity
    # field of its own to map.
    assert findings[0].severity is Severity.high


def test_gitleaks_never_reads_the_fields_that_hold_the_secret(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Layer two of the secret defence: an allowlist at the parse boundary.

    `--redact` should already have scrubbed the report, but the parser must not
    depend on that — it reads RuleID/Description/File/StartLine and nothing else.
    """
    run = _FakeReportRun(_gitleaks_report())
    _patch_tools(monkeypatch, run, present={"gitleaks"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.gitleaks]))

    assert "AKIAIOSFODNN7EXAMPLE" not in format_hints(findings)
    assert "AKIAIOSFODNN7EXAMPLE" not in str(findings)


def test_gitleaks_runs_redacted(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Layer one: the binary must never write the secret down in the first place."""
    run = _FakeReportRun(_gitleaks_report())
    _patch_tools(monkeypatch, run, present={"gitleaks"})

    run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.gitleaks]))

    assert "--redact" in [str(a) for a in run.calls[0]["argv"]]


def test_gitleaks_report_is_written_outside_the_scanned_corpus(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A report inside the corpus would be scanned by the tools running beside it."""
    run = _FakeReportRun(_gitleaks_report())
    _patch_tools(monkeypatch, run, present={"gitleaks"})

    run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.gitleaks]))

    argv = [str(a) for a in run.calls[0]["argv"]]
    report = Path(argv[argv.index("--report-path") + 1])
    corpus = Path(str(run.calls[0]["cwd"]))
    assert corpus not in report.parents


def _astgrep_output() -> str:
    """ast-grep --json=compact. `range.start.line` is 0-BASED, like zizmor's."""
    return json.dumps(
        [
            {
                "ruleId": "no-eval",
                "severity": "error",
                "message": "eval on untrusted input",
                "file": "src/app.py",
                "range": {"start": {"line": 1, "column": 4}},
            }
        ]
    )


def test_astgrep_is_skipped_without_configured_rules(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ast-grep has no built-in rules: with none configured there is nothing to run."""
    run = _FakeRun(outputs={"ast-grep": _astgrep_output()})
    _patch_tools(monkeypatch, run, present={"ast-grep"})

    assert run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.ast_grep])) == []
    assert run.calls == []


def test_astgrep_findings_parsed_with_one_based_lines(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rules = tmp_path / "rules.yml"
    rules.write_text("id: no-eval\n", encoding="utf-8")
    run = _FakeRun(outputs={"ast-grep": _astgrep_output()})
    _patch_tools(monkeypatch, run, present={"ast-grep"})

    findings = run_static_analysis(
        FILES, _cfg(tools=[StaticAnalysisTool.ast_grep], ast_grep_rules=str(rules))
    )

    assert len(findings) == 1
    assert findings[0].rule == "no-eval"
    assert findings[0].path == "src/app.py"
    assert findings[0].line == 2  # 0-based row 1 is line 2
    assert findings[0].severity is Severity.high
    assert str(rules) in [str(a) for a in run.calls[0]["argv"]]


@pytest.mark.skipif(shutil.which("ast-grep") is None, reason="ast-grep not installed")
def test_astgrep_really_matches_a_structural_rule(tmp_path: Path) -> None:
    """A user's own rule, run against the real binary.

    ast-grep exits non-zero when it finds error-level matches and still writes
    valid JSON to stdout — the runner must read the output, not the exit code.
    """
    rules = tmp_path / "rules.yml"
    rules.write_text(
        "id: no-eval\nlanguage: python\nseverity: error\n"
        "message: eval on untrusted input\nrule:\n  pattern: eval($$$ARGS)\n",
        encoding="utf-8",
    )

    findings = run_static_analysis(
        {"src/app.py": "y = 1\nx = eval(input())\n"},
        _cfg(tools=[StaticAnalysisTool.ast_grep], ast_grep_rules=str(rules)),
    )

    assert [(f.rule, f.path, f.line) for f in findings] == [("no-eval", "src/app.py", 2)]


def _zizmor_output() -> str:
    """zizmor --format json. `start_point.row` is 0-BASED; findings are 1-based."""
    return json.dumps(
        [
            {
                "ident": "template-injection",
                "desc": "code injection via template expansion",
                "determinations": {"severity": "High", "confidence": "High"},
                "locations": [
                    {
                        "symbolic": {
                            "key": {"Local": {"verbatim_path": "./.github/workflows/ci.yml"}}
                        },
                        "concrete": {"location": {"start_point": {"row": 6, "column": 8}}},
                    }
                ],
            }
        ]
    )


WORKFLOW_FILES = {".github/workflows/ci.yml": "on: [pull_request_target]\njobs: {}\n"}


def test_zizmor_findings_parsed_with_severity_and_one_based_lines(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun(outputs={"zizmor": _zizmor_output()})
    _patch_tools(monkeypatch, run, present={"zizmor"})

    findings = run_static_analysis(WORKFLOW_FILES, _cfg(tools=[StaticAnalysisTool.zizmor]))

    assert len(findings) == 1
    assert findings[0].rule == "template-injection"
    assert findings[0].path == ".github/workflows/ci.yml"
    # row 6 is zizmor's 0-based index for line 7 — off by one if taken verbatim.
    assert findings[0].line == 7
    assert findings[0].severity is Severity.high


def test_zizmor_runs_offline(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun(outputs={"zizmor": _zizmor_output()})
    _patch_tools(monkeypatch, run, present={"zizmor"})

    run_static_analysis(WORKFLOW_FILES, _cfg(tools=[StaticAnalysisTool.zizmor]))

    argv = [str(a) for a in run.calls[0]["argv"]]
    assert "--offline" in argv, "zizmor must never reach the network from the sandbox"


def test_zizmor_is_skipped_when_no_workflow_file_changed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Not an optimisation: zizmor panics when handed a tree with no workflows.

    Without this the tool would crash on the majority of PRs, degrade to no
    findings through the blanket handler, and log a warning every time.
    """
    run = _FakeRun(outputs={"zizmor": _zizmor_output()})
    _patch_tools(monkeypatch, run, present={"zizmor"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.zizmor]))

    assert findings == []
    assert run.calls == []


@pytest.mark.skipif(shutil.which("zizmor") is None, reason="zizmor not installed")
def test_zizmor_really_flags_template_injection_and_unpinned_uses() -> None:
    """The two workflow bugs the review checklist already names, run for real."""
    workflow = (
        "name: bad\n"
        "on: [pull_request_target]\n"
        "jobs:\n"
        "  x:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        '      - run: echo "${{ github.event.pull_request.title }}"\n'
    )
    findings = run_static_analysis(
        {".github/workflows/bad.yml": workflow}, _cfg(tools=[StaticAnalysisTool.zizmor])
    )

    rules = {f.rule for f in findings}
    assert {"template-injection", "unpinned-uses"} <= rules, rules
    assert all(f.path == ".github/workflows/bad.yml" for f in findings)
    assert all(f.line >= 1 for f in findings)


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_gitleaks_really_finds_a_committed_credential() -> None:
    """The bug this tool exists for, run against the real binary.

    A faked subprocess proves we parse the report; it cannot prove the argv we
    pass still surfaces a hit — the subcommand and report flags have moved
    between gitleaks releases, and a wrong one degrades to silent zero findings,
    which looks exactly like a clean scan.
    """
    # NOT the AWS documentation key (AKIAIOSFODNN7EXAMPLE): gitleaks allowlists
    # well-known example credentials, so using one here would assert nothing.
    secret = "ghp_012345678901234567890123456789abcdef"
    findings = run_static_analysis(
        {"src/settings.py": f'TOKEN = "{secret}"\n'},
        _cfg(tools=[StaticAnalysisTool.gitleaks]),
    )

    assert findings, "gitleaks found nothing — check the argv against this version"
    assert findings[0].path == "src/settings.py"
    assert findings[0].line == 1
    assert secret not in str(findings)


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


def test_semgrep_uses_the_bundled_pack_when_no_rules_are_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The un-crippling: semgrep used to skip itself whenever rules were unset.

    Nobody set `semgrep_rules`, so the one multi-language tool never ran. It now
    falls back to the MIT pack we ship, and only a deliberate override changes
    which rules it uses.
    """
    run = _FakeRun(outputs={"semgrep": json.dumps({"results": []})})
    _patch_tools(monkeypatch, run, present={"semgrep"})

    run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.semgrep]))

    assert run.calls, "semgrep must run without explicitly configured rules"
    argv = [str(a) for a in run.calls[0]["argv"]]
    assert str(static_analysis.bundled_semgrep_rules()) in argv
    assert "--config" in argv
    assert "auto" not in argv, "the network registry is forbidden in the sandbox"


@pytest.mark.skipif(shutil.which("semgrep") is None, reason="semgrep not installed")
def test_bundled_rules_really_catch_their_targets() -> None:
    """The shipped pack, run by the real engine — rules that parse but never
    match would be invisible without this."""
    findings = run_static_analysis(
        {
            "src/app.py": (
                'import subprocess, os\nsubprocess.run("ls " + os.environ["X"], shell=True)\n'
            )
        },
        _cfg(tools=[StaticAnalysisTool.semgrep]),
    )

    assert any("shell-true" in f.rule for f in findings), [f.rule for f in findings]


def _osv_output(root: str) -> str:
    """osv-scanner scan source --format json (v2 shape).

    `source.path` is ABSOLUTE, under the directory scanned — so the parser has
    to relativise it back to the repository path.
    """
    return json.dumps(
        {
            "results": [
                {
                    "source": {"path": f"{root}/uv.lock", "type": "lockfile"},
                    "packages": [
                        {
                            "package": {"name": "jinja2", "version": "2.4.1", "ecosystem": "PyPI"},
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-462w-v97r-4m45",
                                    "summary": "Jinja2 sandbox escape",
                                    "database_specific": {"severity": "HIGH"},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )


MANIFEST_FILES = {"uv.lock": 'name = "jinja2"\nversion = "2.4.1"\n'}


def test_osv_findings_name_the_package_and_advisory(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class _Run(_FakeRun):
        def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
            self.calls.append({"argv": argv, **kwargs})
            return SimpleNamespace(stdout=_osv_output(str(kwargs["cwd"])), stderr="", returncode=0)

    run = _Run()
    _patch_tools(monkeypatch, run, present={"osv-scanner"})

    findings = run_static_analysis(MANIFEST_FILES, _cfg(tools=[StaticAnalysisTool.osv_scanner]))

    assert len(findings) == 1
    assert findings[0].rule == "GHSA-462w-v97r-4m45"
    assert findings[0].path == "uv.lock"
    assert "jinja2" in findings[0].message
    assert "2.4.1" in findings[0].message
    assert findings[0].severity is Severity.high


def test_osv_runs_offline(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The vulnerability database must be local — the sandbox has no network."""
    run = _FakeRun(outputs={"osv-scanner": _osv_output("/x")})
    _patch_tools(monkeypatch, run, present={"osv-scanner"})

    run_static_analysis(MANIFEST_FILES, _cfg(tools=[StaticAnalysisTool.osv_scanner]))

    argv = [str(a) for a in run.calls[0]["argv"]]
    assert "--offline-vulnerabilities" in argv
    assert "--download-offline-databases" not in argv, "downloading is a network call"


def test_osv_is_skipped_when_no_manifest_changed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    run = _FakeRun(outputs={"osv-scanner": _osv_output("/x")})
    _patch_tools(monkeypatch, run, present={"osv-scanner"})

    findings = run_static_analysis(FILES, _cfg(tools=[StaticAnalysisTool.osv_scanner]))

    assert findings == []
    assert run.calls == []
