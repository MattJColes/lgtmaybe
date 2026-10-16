"""Static-analysis fusion (F1): deterministic tool findings as LLM grounding.

Runs fast linters/SAST (ruff, bandit, mypy, and semgrep with local rules) over
the **already-fetched changed-file texts**, written to a throwaway temp dir —
never a checkout, never executing PR code (linting and type checking are
parsing plus inference, like ast-grep; no module is ever imported). The
findings reach the review by the tool's mode: `hint` formats them as untrusted
HINTS for the lens prompts ("confirm, contextualise, or discard"), raising
recall on the deterministic bugs LLMs miss while letting the model suppress raw
linter noise; `finding` maps them straight onto review findings with no model
call at all. Every tool defaults to `hint`; `finding` is a per-tool opt-in (see
`_DEFAULT_MODE`).

Sandboxing posture:

- tools are external binaries discovered on PATH — a missing tool is skipped
  silently, so a minimal install degrades to no hints;
- subprocesses get a **scrubbed environment** (PATH plus process-critical
  Windows variables when applicable; no proxy vars or cloud credentials;
  user profile/config roots pinned inside the sandbox) and a hard timeout;
- the corpus is PR-authored, so no tool may take its config, ignore files or
  inline ignore pragmas from it: each argv pins a sandbox-owned config outside
  the corpus and switches ignore-file discovery and inline pragmas off, and the
  two files no flag can override are withheld from the corpus (see
  `_WITHHELD_NAMES`). Two pragmas have no off switch: mypy's `# type: ignore`
  and ast-grep's `ast-grep-ignore`;
- semgrep always runs against LOCAL rules — the bundled MIT pack by default,
  or `semgrep_rules` — never its registry configs (``--config auto``), which
  fetch over the network;
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
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from lgtmaybe.core.diff import is_scannable_manifest
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    SCAN_CATEGORY_PREFIX,
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
# few prompt tokens, a comment costs a human's attention. Applied by the engine
# AFTER it scopes scan findings to the diff, so the cap is spent on findings
# that can actually post.
MAX_SCAN_FINDINGS = 20

# How each tool's findings reach the review when the user has not overridden it.
#
# Every tool defaults to `hint`. One path for every tool: the model sees each
# deterministic hit beside the code it is about, and confirms, contextualises or
# discards it, so nothing posts without a model having read it in context. A
# user who wants a tool to post directly opts it in per tool, e.g.
# `tool_mode: {gitleaks: finding}`.
#
# Hints on files no batch holds (an osv-scanner advisory against a lockfile,
# which is never reviewed) ride the first batch's security lens instead; see
# `engine._unbatched_hint_block`.
#
# The trade-off, stated honestly: in hint mode the model can talk itself out of
# a real hit (a committed secret is there or it isn't). That is why `finding`
# mode stays available, to post a tool's hits with no model in the way.
_DEFAULT_MODE: dict[StaticAnalysisTool, ToolMode] = {
    StaticAnalysisTool.ruff: ToolMode.hint,
    StaticAnalysisTool.bandit: ToolMode.hint,
    StaticAnalysisTool.mypy: ToolMode.hint,
    StaticAnalysisTool.semgrep: ToolMode.hint,
    StaticAnalysisTool.gitleaks: ToolMode.hint,
    StaticAnalysisTool.zizmor: ToolMode.hint,
    StaticAnalysisTool.ast_grep: ToolMode.hint,
    StaticAnalysisTool.osv_scanner: ToolMode.hint,
}

# Scanners whose findings can never anchor to a changed line, by construction.
# A CVE is about the dependency, not about a position in a resolved lockfile —
# and lockfiles are not reviewable, so their patches never reach the diff the
# engine re-anchors against. The engine exempts these from the rule that scopes
# scan findings to changed lines; the stricter unanchored severity floor then
# keeps them to advisories worth acting on.
UNANCHORABLE_SCAN_CATEGORIES: frozenset[str] = frozenset(
    {f"{SCAN_CATEGORY_PREFIX}{StaticAnalysisTool.osv_scanner.value}"}
)

# Every tool that grades its own findings names the rungs from this set, and on
# the names they share they agree — so one union table serves bandit, zizmor,
# osv-scanner and semgrep. Each tool only ever looks up the names it emits; what
# is genuinely per-tool is the *default* for a name it doesn't (see `_SPECS`).
# Two odd names: zizmor's "Unknown" is an audit declining to commit, so it maps
# to the weakest rung rather than being dropped; and OSV grades advisories on
# GitHub's scale, where "MODERATE" is what every other tool calls medium.
_SEVERITY_BY_NAME = {
    "UNKNOWN": Severity.info,
    "INFORMATIONAL": Severity.info,
    "INFO": Severity.info,
    "LOW": Severity.low,
    "MODERATE": Severity.medium,
    "MEDIUM": Severity.medium,
    "WARNING": Severity.medium,
    "HIGH": Severity.high,
    "ERROR": Severity.high,
    "CRITICAL": Severity.critical,
}

# ast-grep is the one tool that disagrees, on exactly one shared name: it grades
# rules, not diagnostics, so its `info` rung is a real (if minor) defect claim
# rather than the informational aside every other tool means by it — low, not
# info. `hint` is the rule author saying "this is a nudge, not a defect", so it
# floors at info rather than being promoted.
_ASTGREP_SEVERITY = _SEVERITY_BY_NAME | {"INFO": Severity.low, "HINT": Severity.info}

_UNGRADED: Mapping[str, Severity] = {}


class _Spec(NamedTuple):
    """Where one tool's JSON keeps each `ToolFinding` field, as dotted key paths.

    Six of the eight tools report the same five facts under different spellings,
    so they are a table rather than six near-identical functions (`_zizmor_finding`
    and `_osv_findings` have genuinely irregular shapes and stay hand-written).
    `offset` is 1 for a tool reporting 0-based lines. A tool that grades nothing
    itself leaves `severity` empty, so every finding lands on `default`.
    """

    path: str
    line: str
    rule: str
    message: str
    severity: str = ""
    severities: Mapping[str, Severity] = _UNGRADED
    default: Severity = Severity.low
    offset: int = 0
    rule_default: str = ""


_T = StaticAnalysisTool
_SPECS: dict[StaticAnalysisTool, _Spec] = {
    # ruff grades nothing itself, and its hits are lint-grade: real enough to
    # check, rarely a blocker — so every hit lands on that one rung.
    _T.ruff: _Spec("filename", "location.row", "code", "message"),
    _T.bandit: _Spec(
        "filename", "line_number", "test_id", "issue_text", "issue_severity", _SEVERITY_BY_NAME
    ),
    # A type error is a provable contradiction in the code as written, so it
    # outranks a ruff lint (low) — but mypy on a single file out of its project
    # can't see every runtime guard either, so it is not automatically high.
    # A diagnostic with no error code is a `note`: mypy elaborating on the error
    # above it, hence info, and named rather than left an empty rule so the tie
    # to its parent stays visible.
    _T.mypy: _Spec(
        "file",
        "line",
        "code",
        "message",
        "severity",
        {"ERROR": Severity.medium},
        Severity.info,
        rule_default="note",
    ),
    # gitleaks reports `Match`, `Secret` and `Line` too — the credential and the
    # source line around it. `--redact` should already have scrubbed them, but
    # this spec must not depend on that, so it names only the fields that cannot
    # hold the secret. This allowlist is layer two of the secret defence; keep it
    # an allowlist. gitleaks grades nothing: a committed credential is high by
    # definition, and nothing it reports is worth grading lower.
    _T.gitleaks: _Spec("File", "StartLine", "RuleID", "Description", default=Severity.high),
    # ast-grep's `range.start.line` is 0-based, like zizmor's. Note ast-grep exits
    # non-zero when it matches an error-level rule, so the runner must read stdout
    # rather than the exit status — it already does.
    _T.ast_grep: _Spec(
        "file", "range.start.line", "ruleId", "message", "severity", _ASTGREP_SEVERITY, offset=1
    ),
    _T.semgrep: _Spec(
        "path", "start.line", "check_id", "extra.message", "extra.severity", _SEVERITY_BY_NAME
    ),
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
        # Resolved: tools report resolved paths, so on a symlinked temp dir
        # (macOS's /var -> /private/var) an unresolved root never relativises.
        root = Path(tmp).resolve()
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
    StaticAnalysisTool.osv_scanner: is_scannable_manifest,
}


def _has_relevant_input(tool: StaticAnalysisTool, paths: list[str]) -> bool:
    """Whether *tool* has anything in the corpus worth running over."""
    predicate = _RELEVANT_PATHS.get(tool)
    return predicate is None or any(predicate(path) for path in paths)


def bundled_semgrep_rules() -> Path:
    """The MIT semgrep rules shipped inside the package.

    Located relative to this module rather than via importlib.resources: semgrep
    needs a real filesystem path for --config, and this resolves correctly both
    from an installed wheel and from inside the PyInstaller one-file executable
    (where __file__ points into the extracted _MEIPASS tree).

    We ship our own rules because the widely-used upstream packs are LGPL-2.1
    **plus a Commons Clause** — not open source, and not something an MIT wheel
    can redistribute honestly. Point `semgrep_rules` at a local directory to use
    a fuller pack instead.
    """
    return Path(__file__).resolve().parent.parent / "rules" / "semgrep"


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

    Uncapped by design: the cap bounds POSTED comments, and the engine scopes
    scan findings to the diff before capping them (step 6b/6c in ``review``) —
    capping here would spend the cap on off-diff hits the engine is about to
    drop, starving the one new hit the PR actually added.

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
                    f"Reported by `{finding.tool}` (rule `{finding.rule}`): a deterministic "
                    "scan, not a model judgement."
                ),
                anchor=redact(anchor),
                category=f"{SCAN_CATEGORY_PREFIX}{finding.tool}",
            )
        )
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
    # On "\n" alone, as tools number lines and core/diffparse splits them —
    # splitlines() also breaks on a form feed, shifting every anchor below one.
    lines = text.removesuffix("\n").split("\n") if text else []
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


# PR files a tool would read as its own config and that no flag can switch off:
# semgrep always honours a root `.semgrepignore`, and gitleaks reads the scanned
# dir's `.gitleaksignore` even beside `--gitleaks-ignore-path`. Every other
# tool's config is pinned in its argv (see `_pin`); these are kept out of the
# corpus instead — the model still reviews them in the diff.
_WITHHELD_NAMES = frozenset({".semgrepignore", ".gitleaksignore"})


def _pin(report_dir: Path, name: str, text: str) -> str:
    """Write a sandbox-owned tool config into *report_dir*; return its path.

    The corpus is PR-authored, and every tool discovers config from the tree it
    scans — a PR could otherwise switch off the very rule that flags it. The
    report dir is never scanned, so a config pinned there is ours alone.
    """
    path = report_dir / name
    path.write_text(text, encoding="utf-8")
    return str(path)


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
        if rel.name in _WITHHELD_NAMES:
            _log.info("withholding tool config from the corpus", extra={"path": path})
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
        argv = [
            binary,
            "check",
            "--isolated",
            "--output-format",
            "json",
            "--exit-zero",
            "--no-cache",
            # --isolated drops config files but not ignore files: a PR-added
            # `.ignore` would still hide the files it names.
            "--no-respect-gitignore",
            # A `# noqa` in the corpus is the PR waving its own finding through.
            "--ignore-noqa",
            ".",
        ]
    elif tool is StaticAnalysisTool.bandit:
        argv = [
            binary,
            "-f",
            "json",
            "-q",
            # Without --ini bandit adopts any `.bandit` it finds in the corpus.
            "--ini",
            _pin(report_dir, "bandit.ini", "[bandit]\n"),
            # Empty, replacing the default exclude list: its ".git" entry is a
            # substring match, so it silently skipped everything under .github/.
            "--exclude=",
            # Likewise an inline `# nosec`.
            "--ignore-nosec",
            "-r",
            ".",
        ]
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
            "--config-file",
            _pin(report_dir, "mypy.ini", "[mypy]\n"),
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
            # The default rules, never a PR-added `.gitleaks.toml`, and no inline
            # `gitleaks:allow` — a secret the PR waves through itself is still a
            # committed secret. (`.gitleaksignore` is withheld from the corpus.)
            "--config",
            _pin(report_dir, "gitleaks.toml", "[extend]\nuseDefault = true\n"),
            "--ignore-gitleaks-allow",
        ]
    elif tool is StaticAnalysisTool.osv_scanner:
        # --offline-vulnerabilities uses a LOCAL database only. Never
        # --download-offline-databases: that fetches, and the sandbox has no
        # network. With no database present osv reports nothing, which is why a
        # configured-but-unseeded scanner must not read as a clean bill of
        # health — see the missing-database notice.
        argv = [
            binary,
            "scan",
            "source",
            "--format",
            "json",
            "--offline-vulnerabilities",
            # The corpus is loose files, not a resolvable project: without this
            # osv tries to resolve transitive dependencies, which needs network.
            "--no-resolve",
            # An empty config overrides every PR-added `osv-scanner.toml`, whose
            # IgnoredVulns / PackageOverrides would hide advisories; --no-ignore
            # stops a `.gitignore` from hiding the manifest itself.
            "--config",
            _pin(report_dir, "osv-scanner.toml", ""),
            "--no-ignore",
            ".",
        ]
    elif tool is StaticAnalysisTool.ast_grep:
        rules = cfg.static_analysis.ast_grep_rules
        if not rules:
            # ast-grep ships no rules of its own — with none configured there is
            # nothing for it to look for.
            _log.info("ast-grep skipped — no ast_grep_rules configured")
            return []
        # Deliberately a copy of scripts/check_spec_drift.py's invocation rather
        # than an import: that script lives outside the package and importing it
        # would put a dev tool on the runtime import path. Keep the two in step —
        # except the config pin and --no-ignore flags below, which exist because
        # this corpus is PR-authored; the drift script scans our own repo.
        argv = [
            binary,
            "scan",
            "--rule",
            str(Path(rules).resolve()),
            # Our own root config, or a PR-added `sgconfig.yml` is adopted —
            # one `languageGlobs` entry re-types every .py file and blinds it.
            "-c",
            _pin(report_dir, "sgconfig.yml", "ruleDirs: []\n"),
            # Hidden dirs (.github/) are skipped by default and `.ignore` files
            # are honoured; the corpus is exactly the files under review.
            "--no-ignore=hidden",
            "--no-ignore=dot",
            "--json=compact",
            ".",
        ]
    elif tool is StaticAnalysisTool.zizmor:
        # --offline forbids the online audits that resolve actions against the
        # GitHub API; --no-progress keeps the machine-readable output clean.
        # --no-config / --no-ignores: a PR-added zizmor.yml or an inline
        # `# zizmor: ignore[...]` must not disable the audit of that PR's
        # workflows; --collect=all stops a `.gitignore` from hiding them.
        argv = [
            binary,
            "--format",
            "json",
            "--no-progress",
            "--offline",
            "--no-config",
            "--no-ignores",
            "--collect=all",
            ".",
        ]
    else:  # semgrep
        # Unset now means the bundled MIT pack, not "skip". Before, semgrep sat
        # out every review unless someone configured rules, which nobody did —
        # so the one multi-language tool never ran at all. Never `--config auto`:
        # the registry is a network fetch the sandbox forbids.
        configured = cfg.static_analysis.semgrep_rules
        semgrep_rules = Path(configured).resolve() if configured else bundled_semgrep_rules()
        argv = [
            binary,
            "scan",
            "--json",
            "--quiet",
            "--metrics=off",
            "--disable-version-check",
            # The corpus carries no .gitignore of its own, but a PR that changes
            # one puts it there — and semgrep would then honour it and silently
            # skip the very files under review.
            "--no-git-ignore",
            # And an inline `# nosemgrep` / `# nosem`.
            "--disable-nosem",
            "--config",
            str(semgrep_rules),
            ".",
        ]

    try:
        result = subprocess.run(  # noqa: S603 — argv is built above, never from PR data
            argv,
            cwd=str(root),
            env=scrubbed_env(root),
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


def scrubbed_env(root: Path) -> dict[str, str]:
    """A minimal subprocess environment: nothing to phone home with.

    Shared by every engine subprocess that scans untrusted text (static
    analysis, the risk scan, ast-grep), so the scrubbing policy has one
    definition. POSIX retains only PATH. Windows also needs a small set of
    process-critical
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
    # The one variable forwarded from the parent environment. It names a
    # read-only local directory holding the offline vulnerability database —
    # not a credential, not an endpoint — and osv-scanner cannot fetch a
    # database from inside a network-less sandbox. Without it the scanner
    # silently reports nothing, which is indistinguishable from a clean result.
    osv_db = os.environ.get("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY")
    if osv_db:
        env["OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY"] = osv_db
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
        return [
            _table_finding(tool, json.loads(line), root)
            for line in stdout.splitlines()
            if line.strip()
        ]
    data = json.loads(stdout)
    if tool is StaticAnalysisTool.osv_scanner:
        return _osv_findings(data, root)
    if tool is StaticAnalysisTool.zizmor:
        return [_zizmor_finding(item) for item in data or []]
    # The rest are table-driven (`_SPECS`); only the envelope differs. bandit and
    # semgrep nest their findings under "results"; ruff, gitleaks and ast-grep
    # emit a bare array — and gitleaks writes `null` rather than `[]` for none.
    items = data.get("results", []) if isinstance(data, dict) else data or []
    return [_table_finding(tool, item, root) for item in items]


def _posix_rel(path: str, root: Path | None = None) -> str:
    """Map a tool-reported path into the canonical repository path form."""
    p = Path(path)
    if root is not None:
        try:
            p = p.relative_to(root)
        except ValueError:
            pass
    return p.as_posix().replace("\\", "/").removeprefix("./")


def _osv_findings(data: dict[str, Any], root: Path) -> list[ToolFinding]:
    """Flatten osv-scanner's source → packages → vulnerabilities nesting.

    One finding per advisory per package. The line is always 1: a lockfile hunk
    is machine-generated and the useful anchor is the file, not a position in a
    resolved dependency tree — these findings are expected to render in the
    review body rather than inline.
    """
    findings: list[ToolFinding] = []
    for result in data.get("results", []):
        path = _posix_rel(str(result.get("source", {}).get("path", "")), root)
        for entry in result.get("packages", []):
            package = entry.get("package", {})
            name = str(package.get("name", ""))
            version = str(package.get("version", ""))
            for vuln in entry.get("vulnerabilities", []):
                severity = str(vuln.get("database_specific", {}).get("severity", "")).upper()
                summary = str(vuln.get("summary") or "known vulnerability")
                findings.append(
                    ToolFinding(
                        tool="osv-scanner",
                        path=path,
                        line=1,
                        rule=str(vuln.get("id", "")),
                        message=f"{name} {version}: {summary}",
                        severity=_SEVERITY_BY_NAME.get(severity, Severity.medium),
                    )
                )
    return findings


def _zizmor_finding(item: dict[str, Any]) -> ToolFinding:
    """Map one zizmor audit hit onto the shared scale.

    Three shape traps, all load-bearing: ``start_point.row`` is **0-based**, the
    path hides under a single-key enum wrapper (``Local``/``Remote``) whose
    inner key varies by input kind, and the first location is usually the
    ``Hidden`` one ("this step" / "this workflow") — the ``Primary`` one points
    at the offending line, so anchoring on ``locations[0]`` lands the comment
    on an unchanged line and the engine drops it as off-diff.
    """
    locations = item.get("locations") or [{}]
    chosen = next(
        (loc for loc in locations if loc.get("symbolic", {}).get("kind") == "Primary"),
        locations[0],
    )
    concrete = chosen.get("concrete", {}).get("location", {})
    key = chosen.get("symbolic", {}).get("key", {})
    inner: Any = next(iter(key.values()), {}) if isinstance(key, dict) else {}
    path = next(iter(inner.values()), "") if isinstance(inner, dict) else ""
    severity = str(item.get("determinations", {}).get("severity", "")).upper()
    return ToolFinding(
        tool="zizmor",
        path=_posix_rel(str(path)),
        line=int(concrete.get("start_point", {}).get("row", 0)) + 1,
        rule=str(item.get("ident", "")),
        message=str(item.get("desc", "")),
        severity=_SEVERITY_BY_NAME.get(severity, Severity.low),
    )


def _get(item: dict[str, Any], path: str) -> Any:
    """Read a dotted key path out of a tool's JSON; any missing level is None.

    An empty path reads nothing, which is how an ungraded tool (one with no
    ``severity`` key of its own) lands on its spec's `default_severity`.
    """
    node: Any = item
    for key in path.split("."):
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _table_finding(tool: StaticAnalysisTool, item: dict[str, Any], root: Path) -> ToolFinding:
    """Build a `ToolFinding` from one tool's JSON object via its `_Spec`."""
    spec = _SPECS[tool]
    line = _get(item, spec.line)
    return ToolFinding(
        tool=tool.value,
        path=_posix_rel(str(_get(item, spec.path) or ""), root),
        line=int(line) + spec.offset if line else 1,
        rule=str(_get(item, spec.rule) or spec.rule_default),
        message=str(_get(item, spec.message) or ""),
        severity=spec.severities.get(str(_get(item, spec.severity) or "").upper(), spec.default),
    )
