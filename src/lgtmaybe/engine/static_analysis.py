"""Static-analysis fusion (F1): deterministic tool findings as LLM grounding.

Runs fast linters/SAST (ruff, bandit, and semgrep with local rules) over the
**already-fetched changed-file texts**, written to a throwaway temp dir — never
a checkout, never executing PR code (linting is parsing, like ast-grep). The
findings are formatted as untrusted HINTS for the lens prompts ("confirm,
contextualise, or discard"), raising recall on the deterministic bugs LLMs miss
while letting the model suppress raw linter noise.

Sandboxing posture:

- tools are external binaries discovered on PATH — a missing tool is skipped
  silently, so a minimal install degrades to no hints;
- subprocesses get a **scrubbed environment** (only PATH; no proxy vars or
  cloud credentials to phone home with; HOME pinned inside the sandbox so
  user-level tool config can't leak in) and a hard timeout;
- semgrep runs only with locally configured rules — its registry configs
  (``--config auto``) fetch over the network, which is forbidden here;
- tool output is untrusted text: the engine redacts it and wraps it in a
  neutralised injection block before it reaches the model, and it is never
  posted as a finding verbatim.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import ReviewConfig, Severity, StaticAnalysisTool

_log = get_logger(__name__)

# Hard per-tool wall-clock cap: a hung linter must never hang the review.
_TOOL_TIMEOUT = 60

# Ceiling on hints handed to the prompts, most severe first. Beyond this a
# noisy lint run is compressed rather than allowed to crowd out the diff.
MAX_HINTS = 50

_BANDIT_SEVERITY = {
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
    if not sa.enabled or not file_contents:
        return []

    findings: list[ToolFinding] = []
    with tempfile.TemporaryDirectory(prefix="lgtmaybe-sa-") as tmp:
        root = Path(tmp)
        written = _write_corpus(root, file_contents)
        if not written:
            return []
        for tool in sa.tools:
            findings.extend(_run_tool(tool, root, cfg))

    kept = [f for f in findings if f.severity >= sa.min_severity]
    dropped = len(findings) - len(kept)
    if findings:
        _log.info(
            "static analysis complete",
            extra={"hints": len(kept), "below_floor": dropped},
        )
    return kept


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
        if rel.is_absolute() or ".." in rel.parts:
            _log.warning("skipping unsafe static-analysis path", extra={"path": path})
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        written.append(path)
    return written


def _run_tool(tool: StaticAnalysisTool, root: Path, cfg: ReviewConfig) -> list[ToolFinding]:
    """Run one tool over the corpus at *root*; any failure degrades to []."""
    binary = shutil.which(tool.value)
    if binary is None:
        _log.info("static-analysis tool not installed — skipping", extra={"tool": tool.value})
        return []

    if tool is StaticAnalysisTool.ruff:
        argv = [binary, "check", "--output-format", "json", "--exit-zero", "--no-cache", "."]
    elif tool is StaticAnalysisTool.bandit:
        argv = [binary, "-f", "json", "-q", "-r", "."]
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
            timeout=_TOOL_TIMEOUT,
        )
        return _parse_output(tool, result.stdout, root)
    except Exception as exc:  # noqa: BLE001 — grounding is best-effort, never fatal
        _log.warning(
            "static-analysis tool failed — skipping",
            extra={"tool": tool.value, "error": str(exc)[:200]},
        )
        return []


def _scrubbed_env(root: Path) -> dict[str, str]:
    """A minimal subprocess environment: nothing to phone home with.

    Only PATH survives from the parent (to find the binary's own deps); proxy
    variables, cloud credentials, and tokens are all dropped, and HOME is
    pinned inside the sandbox so user-level tool config can't alter behaviour.
    """
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
        "NO_COLOR": "1",
        # Belt and braces for semgrep even with --metrics=off.
        "SEMGREP_SEND_METRICS": "off",
    }


def _parse_output(tool: StaticAnalysisTool, stdout: str, root: Path) -> list[ToolFinding]:
    data = json.loads(stdout)
    if tool is StaticAnalysisTool.ruff:
        return [_ruff_finding(item, root) for item in data]
    if tool is StaticAnalysisTool.bandit:
        return [_bandit_finding(item) for item in data.get("results", [])]
    return [_semgrep_finding(item) for item in data.get("results", [])]


def _relativise(path: str, root: Path) -> str:
    """Map a tool-reported path back to the repo-relative form."""
    p = Path(path)
    try:
        return str(p.relative_to(root))
    except ValueError:
        # Already relative (bandit reports ./src/x.py; semgrep src/x.py).
        return str(p).removeprefix("./")


def _ruff_finding(item: dict[str, Any], root: Path) -> ToolFinding:
    return ToolFinding(
        tool="ruff",
        path=_relativise(str(item.get("filename", "")), root),
        line=int(item.get("location", {}).get("row", 1)),
        rule=str(item.get("code") or ""),
        message=str(item.get("message", "")),
        # ruff hits are lint-grade: real enough to check, rarely a blocker.
        severity=Severity.low,
    )


def _bandit_finding(item: dict[str, Any]) -> ToolFinding:
    return ToolFinding(
        tool="bandit",
        path=str(item.get("filename", "")).removeprefix("./"),
        line=int(item.get("line_number", 1)),
        rule=str(item.get("test_id", "")),
        message=str(item.get("issue_text", "")),
        severity=_BANDIT_SEVERITY.get(str(item.get("issue_severity", "")).upper(), Severity.low),
    )


def _semgrep_finding(item: dict[str, Any]) -> ToolFinding:
    extra = item.get("extra", {})
    return ToolFinding(
        tool="semgrep",
        path=str(item.get("path", "")).removeprefix("./"),
        line=int(item.get("start", {}).get("line", 1)),
        rule=str(item.get("check_id", "")),
        message=str(extra.get("message", "")),
        severity=_SEMGREP_SEVERITY.get(str(extra.get("severity", "")).upper(), Severity.low),
    )
