"""Static-analysis fusion (F1): deterministic tool findings as LLM grounding.

Runs fast linters/SAST (ruff, bandit, mypy, and semgrep with local rules) over
the **already-fetched changed-file texts**, written to a throwaway temp dir —
never a checkout, never executing PR code (linting and type checking are
parsing plus inference, like ast-grep; no module is ever imported). The
findings reach the review by the tool's mode: `hint` formats them as untrusted
HINTS for the lens prompts ("confirm, contextualise, or discard"), raising
recall on the deterministic bugs LLMs miss while letting the model suppress raw
linter noise; `finding` maps them straight onto review findings with no model
call at all, for tools whose claims need no interpretation (see `_DEFAULT_MODE`).

Sandboxing posture:

- tools are external binaries discovered on PATH — a missing tool is skipped
  silently, so a minimal install degrades to no hints;
- subprocesses get a **scrubbed environment** (PATH plus process-critical
  Windows variables when applicable; no proxy vars or cloud credentials;
  user profile/config roots pinned inside the sandbox) and a hard timeout;
- semgrep runs only with locally configured rules — its registry configs
  (``--config auto``) fetch over the network, which is forbidden here;
- tool output is untrusted text: a `hint`-mode tool's output is redacted and
  wrapped in a neutralised injection block before it reaches the model; a
  `finding`-mode tool's output is redacted before it becomes a finding, and its
  raw text is never posted verbatim either way.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    _SCAN_CATEGORY_PREFIX,
    ReviewConfig,
    ReviewFinding,
    Severity,
    StaticAnalysisTool,
    ToolMode,
)

from .redact import redact

_log = get_logger(__name__)
_WINDOWS = os.name == "nt"

# Hard per-tool wall-clock cap: a hung linter must never hang the review. Large
# enough that semgrep over a many-file change still finishes — a tool killed
# mid-scan degrades to no hints at all, silently.
_TOOL_TIMEOUT = 180

# Ceiling on hints handed to the prompts, most severe first. Beyond this a
# noisy lint run is compressed rather than allowed to crowd out the diff.
MAX_HINTS = 50

# Ceiling on findings posted directly, most severe first. Direct posts never
# pass through the model, so nothing else bounds them: one over-broad rule pack
# on a large PR would otherwise open a comment per hit and burn the reviewer's
# credibility in a single run. Far below MAX_HINTS on purpose — a hint costs a
# few prompt tokens, a comment costs a human's attention.
MAX_SCAN_FINDINGS = 20

# How each tool's findings reach the review when the user has not overridden it.
#
# The split is "does this tool make a claim that is true or false with no
# interpretation?". A secret is committed or it isn't; the user's own structural
# rule matched or it didn't — routing those through a model to be "confirmed"
# adds latency and a chance of the model talking itself out of a real hit. A
# ruff lint or a SAST heuristic is the opposite: often technically true and
# beside the point, which is exactly what the model is good at filtering.
_DEFAULT_MODE: dict[StaticAnalysisTool, ToolMode] = {
    StaticAnalysisTool.ruff: ToolMode.hint,
    StaticAnalysisTool.bandit: ToolMode.hint,
    StaticAnalysisTool.mypy: ToolMode.hint,
    StaticAnalysisTool.semgrep: ToolMode.hint,
    StaticAnalysisTool.gitleaks: ToolMode.finding,
    StaticAnalysisTool.zizmor: ToolMode.finding,
}

# Category prefix for a finding that came from a tool rather than a lens. Keeps
# `finding_rules` able to target a specific scanner, and keeps scan findings out
# of the built-in defect categories whose failure_scenario gate they'd fail.
# Defined in core.models so the CustomLens validator can reserve it there.
SCAN_CATEGORY_PREFIX = _SCAN_CATEGORY_PREFIX

_BANDIT_SEVERITY = {
    "LOW": Severity.low,
    "MEDIUM": Severity.medium,
    "HIGH": Severity.high,
}

# zizmor grades its own audits; "Unknown" is its default when an audit
# declines to commit, so it maps to the weakest rung rather than being dropped.
_ZIZMOR_SEVERITY = {
    "UNKNOWN": Severity.info,
    "INFORMATIONAL": Severity.info,
    "LOW": Severity.low,
    "MEDIUM": Severity.medium,
    "HIGH": Severity.high,
}

_SEMGREP_SEVERITY = {
    "INFO": Severity.info,
    "WARNING": Severity.medium,
    "ERROR": Severity.high,
}


@dataclass(frozen=True)
class ToolFinding:
    """One deterministic tool finding, mapped onto the shared severity scale."""

    tool: str
    path: str
    line: int
    rule: str
    message: str
    severity: Severity


def run_static_analysis(file_contents: dict[str, str], cfg: ReviewConfig) -> list[ToolFinding]:
    """Run the enabled tools over *file_contents* and return their findings.

    Returns ``[]`` — with no subprocess started — when the feature is disabled
    (the default), there are no file texts, or no enabled tool is installed.
    Every per-tool failure (missing binary, crash, timeout, garbage output)
    degrades to no hints from that tool; static analysis is grounding, never
    worth failing a review over.
    """
    sa = cfg.static_analysis
    # `not sa.tools` guards the executor below (max_workers must be > 0) and,
    # more usefully, skips writing a corpus nothing would ever read.
    if not sa.enabled or not sa.tools or not file_contents:
        return []

    findings: list[ToolFinding] = []
    # Two directories, deliberately siblings. Tools that report to a file rather
    # than stdout (gitleaks) must not write into the corpus: the tools run
    # concurrently over that same tree, so a report landing there would be
    # scanned by whichever tool started after it — a secret-bearing report is
    # the worst possible thing to feed back into the scan.
    with (
        tempfile.TemporaryDirectory(prefix="lgtmaybe-sa-", ignore_cleanup_errors=True) as tmp,
        tempfile.TemporaryDirectory(prefix="lgtmaybe-report-", ignore_cleanup_errors=True) as rpt,
    ):
        root = Path(tmp)
        report_dir = Path(rpt)
        written = _write_corpus(root, file_contents)
        if not written:
            return []
        # Each tool is an independent subprocess reading the same corpus
        # read-only, and each carries its own _TOOL_TIMEOUT — run serially their
        # worst cases stack. `map` yields in submission order, so the hint list
        # stays ordered by `sa.tools` whatever order the processes finish in:
        # hints feed the prompt, and a prompt that reorders run to run would
        # bust the shared-prefix cache for nothing.
        with ThreadPoolExecutor(max_workers=len(sa.tools)) as pool:
            for tool_findings in pool.map(
                lambda tool: _run_tool(tool, root, report_dir, written, cfg), sa.tools
            ):
                findings.extend(tool_findings)

    # Per-tool floors win over the global floor, in either direction.
    floors = {tool.value: floor for tool, floor in sa.tool_min_severity.items()}
    kept = [f for f in findings if f.severity >= floors.get(f.tool, sa.min_severity)]
    dropped = len(findings) - len(kept)
    if findings:
        _log.info(
            "static analysis complete",
            extra={"hints": len(kept), "below_floor": dropped},
        )
    return kept


# Tools that only have something to say about particular files. Running one
# over a corpus with none of them is not merely wasted work: zizmor panics when
# handed a tree containing no workflows, which would degrade to a warning on the
# majority of PRs.
_WORKFLOW_PREFIX = ".github/workflows/"
_RELEVANT_PATHS: dict[StaticAnalysisTool, Callable[[str], bool]] = {
    StaticAnalysisTool.zizmor: lambda path: path.startswith(_WORKFLOW_PREFIX),
}


def _has_relevant_input(tool: StaticAnalysisTool, paths: list[str]) -> bool:
    """Whether *tool* has anything in the corpus worth running over."""
    predicate = _RELEVANT_PATHS.get(tool)
    return predicate is None or any(predicate(path) for path in paths)


def mode_for(tool: StaticAnalysisTool, cfg: ReviewConfig) -> ToolMode:
    """How *tool*'s findings reach the review: the user's choice, else the default."""
    return cfg.static_analysis.tool_mode.get(tool, _DEFAULT_MODE[tool])


def partition_by_mode(
    findings: list[ToolFinding], cfg: ReviewConfig
) -> tuple[list[ToolFinding], list[ToolFinding]]:
    """Split tool findings into ``(hints, direct)``, preserving order in both.

    A tool whose name doesn't map to a known member can only come from a future
    or hand-built ``ToolFinding``; treat it as a hint, the conservative side —
    grounding costs a few prompt tokens, a wrong direct post costs trust.
    """
    hints: list[ToolFinding] = []
    direct: list[ToolFinding] = []
    for finding in findings:
        try:
            tool = StaticAnalysisTool(finding.tool)
        except ValueError:
            hints.append(finding)
            continue
        (direct if mode_for(tool, cfg) is ToolMode.finding else hints).append(finding)
    return hints, direct


def tool_review_findings(
    findings: list[ToolFinding], file_contents: dict[str, str]
) -> list[ReviewFinding]:
    """Map ``finding``-mode tool output onto postable review findings.

    No model sees these, so this function carries the whole safety contract:
    every message is redacted (a tool can quote hostile or secret-bearing code),
    the category is namespaced per tool, and a finding whose line does not exist
    in the fetched text is dropped rather than anchored to a guess.

    The anchor is the source line, redacted. Redaction is not optional here and
    is not only a safety measure: the engine re-anchors against the **redacted**
    diff (``engine.review`` snaps against ``redact(ctx.diff)``), so a raw anchor
    from a line holding a credential would match nothing and the finding would
    be dropped as unanchored. Redacting it makes the anchor match *and* means a
    secret never enters a ``ReviewFinding`` at all — the corpus these tools scan
    is deliberately un-redacted, since that is how they find secrets in the
    first place.
    """
    ordered = sorted(findings, key=lambda f: f.severity.rank, reverse=True)
    kept: list[ReviewFinding] = []
    for finding in ordered:
        anchor = _corpus_line(finding, file_contents)
        if anchor is None:
            continue
        kept.append(
            ReviewFinding(
                path=finding.path,
                line=finding.line,
                # The corpus is head text; there is no LEFT-side equivalent.
                side="RIGHT",
                severity=finding.severity,
                # Deterministic, so finding_fingerprint(path, title) is stable
                # across re-runs and the ignore / feedback channels keep working.
                title=f"{finding.tool}: {finding.rule}",
                body=(
                    f"{redact(finding.message)}\n\n"
                    f"Reported by `{finding.tool}` (rule `{finding.rule}`) — a deterministic "
                    "scan, not a model judgement."
                ),
                anchor=redact(anchor),
                category=f"{SCAN_CATEGORY_PREFIX}{finding.tool}",
            )
        )
        if len(kept) == MAX_SCAN_FINDINGS:
            _log.info(
                "scan findings capped",
                extra={"kept": MAX_SCAN_FINDINGS, "considered": len(ordered)},
            )
            break
    return kept


def _corpus_line(finding: ToolFinding, file_contents: dict[str, str]) -> str | None:
    """The verbatim source line *finding* points at, or None if it doesn't exist.

    A tool can report past the end of a file we fetched (truncated content, or a
    path we never fetched at all). Emitting a bogus anchor would make the engine
    fail to match and quietly downgrade the finding; dropping it is honest.
    """
    text = file_contents.get(finding.path)
    if text is None:
        return None
    lines = text.splitlines()
    if not 1 <= finding.line <= len(lines):
        _log.info(
            "scan finding points outside the fetched text — dropping",
            extra={"path": finding.path, "line": finding.line},
        )
        return None
    return lines[finding.line - 1]


def format_hints(findings: list[ToolFinding]) -> str:
    """Render *findings* as the hint lines handed (redacted + wrapped) to the model.

    Most severe first, capped at :data:`MAX_HINTS` so a noisy lint run can't
    crowd the diff out of the prompt.
    """
    ordered = sorted(findings, key=lambda f: f.severity.rank, reverse=True)
    if len(ordered) > MAX_HINTS:
        _log.info(
            "static-analysis hints capped",
            extra={"kept": MAX_HINTS, "dropped": len(ordered) - MAX_HINTS},
        )
        ordered = ordered[:MAX_HINTS]
    return "\n".join(
        f"- [{f.severity}] {f.tool} {f.rule} at {f.path}:{f.line} — {f.message}" for f in ordered
    )


def _write_corpus(root: Path, file_contents: dict[str, str]) -> list[str]:
    """Write the fetched file texts under *root*, refusing paths that escape it.

    Paths come from PR data, so a hostile ``../…`` or absolute path must never
    be written outside the sandbox dir — such entries are skipped (logged).
    Returns the relative paths actually written.
    """
    written: list[str] = []
    for path, text in file_contents.items():
        rel = Path(path)
        if rel.anchor or ".." in rel.parts:
            _log.warning("skipping unsafe static-analysis path", extra={"path": path})
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def _run_tool(
    tool: StaticAnalysisTool,
    root: Path,
    report_dir: Path,
    paths: list[str],
    cfg: ReviewConfig,
) -> list[ToolFinding]:
    """Run one tool over the corpus at *root*; any failure degrades to []."""
    if not _has_relevant_input(tool, paths):
        _log.info("no relevant files for tool — skipping", extra={"tool": tool.value})
        return []
    binary = shutil.which(tool.value)
    if binary is None:
        # A finding-mode tool produces no hints to miss — it produces no
        # findings, which is a coverage hole rather than lost grounding. Say so
        # at warning level, but only when the user asked for this tool by name:
        # `tools` defaults to every member, so a default config on a machine
        # with one linter installed would otherwise warn about all the others.
        sa = cfg.static_analysis
        named = "tools" in sa.model_fields_set and tool in sa.tools
        loud = mode_for(tool, cfg) is ToolMode.finding and (named or tool in sa.tool_mode)
        (_log.warning if loud else _log.info)(
            "static-analysis tool not installed — skipping", extra={"tool": tool.value}
        )
        return []

    # Report file for tools that write findings to a path rather than stdout.
    report = report_dir / f"{tool.value}.json"

    if tool is StaticAnalysisTool.ruff:
        argv = [binary, "check", "--output-format", "json", "--exit-zero", "--no-cache", "."]
    elif tool is StaticAnalysisTool.bandit:
        argv = [binary, "-f", "json", "-q", "-r", "."]
    elif tool is StaticAnalysisTool.mypy:
        # The corpus holds only the CHANGED files, so everything they import is
        # absent by construction. Without these flags mypy reports one error per
        # unresolvable import and the real findings drown: --ignore-missing-imports
        # silences the absent third-party/stdlib stubs, --follow-imports=skip stops
        # it chasing sibling modules that were never fetched. What survives is
        # exactly what it can prove from a single file's own text — which is where
        # the unguarded-Optional bugs live.
        argv = [
            binary,
            "--output",
            "json",
            "--ignore-missing-imports",
            "--follow-imports=skip",
            "--no-error-summary",
            "--no-color-output",
            # Never read or write a cache: the corpus is a throwaway temp dir and
            # a stale cache keyed to another run's files would be worse than none.
            "--no-incremental",
            ".",
        ]
    elif tool is StaticAnalysisTool.gitleaks:
        # --redact is the first of the secret-defence layers: it makes the
        # binary write "REDACTED" in place of every match, so the credential
        # never reaches a file we then read. --no-git because the corpus is
        # loose file texts, not a repository; --exit-code 0 because findings
        # are a normal result here, not a failure.
        argv = [
            binary,
            "dir",
            ".",
            "--redact",
            "--no-banner",
            "--exit-code",
            "0",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ]
    elif tool is StaticAnalysisTool.zizmor:
        # --offline forbids the online audits that resolve actions against the
        # GitHub API; --no-progress keeps the machine-readable output clean.
        argv = [binary, "--format", "json", "--no-progress", "--offline", "."]
    else:  # semgrep
        rules = cfg.static_analysis.semgrep_rules
        if not rules:
            # Without local rules semgrep would need its network registry
            # (--config auto), which the sandbox forbids — skip silently.
            _log.info("semgrep skipped — no local semgrep_rules configured")
            return []
        argv = [
            binary,
            "scan",
            "--json",
            "--quiet",
            "--metrics=off",
            "--disable-version-check",
            "--config",
            str(Path(rules).resolve()),
            ".",
        ]

    try:
        result = subprocess.run(  # noqa: S603 — argv is built above, never from PR data
            argv,
            cwd=str(root),
            env=_scrubbed_env(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TOOL_TIMEOUT,
        )
        # Tools that report to a path leave stdout empty. Explicit encoding: the
        # Windows leg runs with locale-default encoding and a bare read_text()
        # there is exactly what tests/test_code_quality.py fails the build over.
        raw = (
            report.read_text(encoding="utf-8")
            if tool is StaticAnalysisTool.gitleaks
            else result.stdout
        )
        return _parse_output(tool, raw, root)
    except Exception as exc:  # noqa: BLE001 — grounding is best-effort, never fatal
        # str(exc) only — never stdout/stderr, which for a secret scanner can
        # quote the credential it just found.
        _log.warning(
            "static-analysis tool failed — skipping",
            extra={"tool": tool.value, "error": str(exc)[:200]},
        )
        return []


def _scrubbed_env(root: Path) -> dict[str, str]:
    """A minimal subprocess environment: nothing to phone home with.

    POSIX retains only PATH. Windows also needs a small set of process-critical
    system variables; user profile/config roots stay pinned inside the sandbox.
    Proxy variables, cloud credentials, and tokens are always dropped.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
        "NO_COLOR": "1",
        # Belt and braces for semgrep even with --metrics=off.
        "SEMGREP_SEND_METRICS": "off",
    }
    if not _WINDOWS:
        return env

    system_vars = {
        "SystemRoot": os.environ.get("SystemRoot", os.environ.get("SYSTEMROOT")),
        "COMSPEC": os.environ.get("COMSPEC"),
        "PATHEXT": os.environ.get("PATHEXT"),
        "TEMP": os.environ.get("TEMP"),
        "TMP": os.environ.get("TMP"),
    }
    env.update({key: value for key, value in system_vars.items() if value is not None})
    env.update(
        {
            "USERPROFILE": str(root),
            "APPDATA": str(root),
            "LOCALAPPDATA": str(root),
        }
    )
    return env


def _parse_output(tool: StaticAnalysisTool, stdout: str, root: Path) -> list[ToolFinding]:
    # mypy is the odd one out: JSON Lines, one object per diagnostic, where the
    # others emit a single document. Parsed as one it raises and the tool
    # degrades to silence.
    if tool is StaticAnalysisTool.mypy:
        return [_mypy_finding(json.loads(line)) for line in stdout.splitlines() if line.strip()]
    data = json.loads(stdout)
    if tool is StaticAnalysisTool.ruff:
        return [_ruff_finding(item, root) for item in data]
    if tool is StaticAnalysisTool.gitleaks:
        # gitleaks emits a bare array, and `null` for "no findings".
        return [_gitleaks_finding(item) for item in data or []]
    if tool is StaticAnalysisTool.zizmor:
        return [_zizmor_finding(item) for item in data or []]
    if tool is StaticAnalysisTool.bandit:
        return [_bandit_finding(item) for item in data.get("results", [])]
    return [_semgrep_finding(item) for item in data.get("results", [])]


def _posix_rel(path: str, root: Path | None = None) -> str:
    """Map a tool-reported path into the canonical repository path form."""
    p = Path(path)
    if root is not None:
        try:
            p = p.relative_to(root)
        except ValueError:
            pass
    return p.as_posix().replace("\\", "/").removeprefix("./")


def _ruff_finding(item: dict[str, Any], root: Path) -> ToolFinding:
    return ToolFinding(
        tool="ruff",
        path=_posix_rel(str(item.get("filename", "")), root),
        line=int(item.get("location", {}).get("row", 1)),
        rule=str(item.get("code") or ""),
        message=str(item.get("message", "")),
        # ruff hits are lint-grade: real enough to check, rarely a blocker.
        severity=Severity.low,
    )


def _bandit_finding(item: dict[str, Any]) -> ToolFinding:
    return ToolFinding(
        tool="bandit",
        path=_posix_rel(str(item.get("filename", ""))),
        line=int(item.get("line_number", 1)),
        rule=str(item.get("test_id", "")),
        message=str(item.get("issue_text", "")),
        severity=_BANDIT_SEVERITY.get(str(item.get("issue_severity", "")).upper(), Severity.low),
    )


def _mypy_finding(item: dict[str, Any]) -> ToolFinding:
    severity = str(item.get("severity", "")).lower()
    return ToolFinding(
        tool="mypy",
        path=_posix_rel(str(item.get("file", ""))),
        line=int(item.get("line") or 1),
        # A diagnostic without an error code is a `note` — mypy's follow-up
        # explanation of the error above it. Keep the tie to its parent visible
        # rather than rendering an empty rule.
        rule=str(item.get("code") or "note"),
        message=str(item.get("message", "")),
        # A type error is a provable contradiction in the code as written, so it
        # outranks a ruff lint (low) — but mypy on a single file out of its
        # project also can't see every runtime guard, so it is not automatically
        # high. Notes only elaborate on the error they follow: info.
        severity=Severity.medium if severity == "error" else Severity.info,
    )


def _gitleaks_finding(item: dict[str, Any]) -> ToolFinding:
    """Map one gitleaks hit, reading only the fields that cannot hold the secret.

    The report also carries ``Match``, ``Secret`` and ``Line`` — the credential
    and the source line around it. ``--redact`` should already have scrubbed
    them, but this parser must not depend on that, so it never reads them. This
    allowlist is layer two of the secret defence; keep it an allowlist.
    """
    return ToolFinding(
        tool="gitleaks",
        path=_posix_rel(str(item.get("File", ""))),
        line=int(item.get("StartLine") or 1),
        rule=str(item.get("RuleID", "")),
        message=str(item.get("Description", "")),
        # gitleaks has no severity of its own: a committed credential is high by
        # definition, and nothing it reports is worth grading lower.
        severity=Severity.high,
    )


def _zizmor_finding(item: dict[str, Any]) -> ToolFinding:
    """Map one zizmor audit hit onto the shared scale.

    Two shape traps, both load-bearing: ``start_point.row`` is **0-based**, and
    the path hides under a single-key enum wrapper (``Local``/``Remote``) whose
    inner key varies by input kind — so read the one value rather than guessing
    the name.
    """
    location = item.get("locations") or [{}]
    concrete = location[0].get("concrete", {}).get("location", {})
    key = location[0].get("symbolic", {}).get("key", {})
    inner: Any = next(iter(key.values()), {}) if isinstance(key, dict) else {}
    path = next(iter(inner.values()), "") if isinstance(inner, dict) else ""
    severity = str(item.get("determinations", {}).get("severity", "")).upper()
    return ToolFinding(
        tool="zizmor",
        path=_posix_rel(str(path)),
        line=int(concrete.get("start_point", {}).get("row", 0)) + 1,
        rule=str(item.get("ident", "")),
        message=str(item.get("desc", "")),
        severity=_ZIZMOR_SEVERITY.get(severity, Severity.low),
    )


def _semgrep_finding(item: dict[str, Any]) -> ToolFinding:
    extra = item.get("extra", {})
    return ToolFinding(
        tool="semgrep",
        path=_posix_rel(str(item.get("path", ""))),
        line=int(item.get("start", {}).get("line", 1)),
        rule=str(item.get("check_id", "")),
        message=str(extra.get("message", "")),
        severity=_SEMGREP_SEVERITY.get(str(extra.get("severity", "")).upper(), Severity.low),
    )
