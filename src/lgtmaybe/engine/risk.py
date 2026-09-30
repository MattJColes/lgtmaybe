"""Risk of change: how far could this pull request reach?

A review finds bugs in the lines it reads; this sizes the change itself, as one
verdict — ``low``, ``medium``, ``high`` or ``critical`` — with the reasons that
produced it. Four factors, all deterministic:

- **blast radius**: how many workspace files import each changed file, found by
  one ast-grep scan (Python, TypeScript, JavaScript), plus the user's
  ``risk.core_paths`` globs for the hubs an import count can't see;
- **High Impact path signals** (``high_impact.path_signals``), the same regexes
  that floor the overview's High Impact Areas;
- **change size**;
- **code changed with no test file changed**.

No model call, on purpose: the verdict is free (so it can be on for every
review), identical on every re-run of a head (so a push can't flip it), and the
same wherever it renders — the summary line, the overview, a hidden marker, a
label — because every surface reads one :class:`RiskAssessment`.

The scan parses the workspace and executes nothing: on ``pull_request_target``
that is the trusted base checkout, locally the repo itself. Its output only
feeds counts and rendered reasons and never reaches a prompt. When a blast
radius cannot be measured the file is named as unassessed rather than scored —
an unmeasured file must never read as a measured clean one.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from lgtmaybe.core.diffparse import changed_line_count, split_by_file
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    PRContext,
    ReviewConfig,
    RiskAssessment,
    RiskLevel,
    RiskReason,
)

from .astgrep import _find_binary, iter_matches
from .static_analysis import _scrubbed_env

_log = get_logger(__name__)

#: (binary, inline rules YAML, workspace root) -> ast-grep's JSON stdout, or
#: None when the scan did not complete (timeout, non-zero exit, no binary).
#: Injected so tests can stand in a failure without shelling out.
ImportScanner = Callable[[str, str, Path], "str | None"]

#: A structural scan of a large repo takes seconds; this only caps a
#: pathological one, which then reads as unassessed rather than stalling a review.
SCAN_TIMEOUT_SECONDS = 20

_LEVELS: tuple[RiskLevel, ...] = ("low", "medium", "high", "critical")
_RANK = {level: rank for rank, level in enumerate(_LEVELS)}

# Blast radius thresholds (importer counts) and the other factors' bars. The
# spec's contract, not config: tune them from real repos, not per team.
_MEDIUM_IMPORTERS = 5
_HIGH_IMPORTERS = 20
_LARGE_CHANGE_LINES = 500
_UNTESTED_CODE_LINES = 50

# High Impact areas whose path alone makes a change high risk; every other
# area raises it to medium.
_HIGH_AREAS = frozenset({"infrastructure", "security", "data_migration", "backup_and_recovery"})

# Reasons of equal level rank in this order: the most specific signal first.
_FACTOR_ORDER = {"blast_radius": 0, "area": 1, "size": 2, "untested": 3, "scope": 4}

_PY_EXTS = (".py",)
_JS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_CODE_EXTS = frozenset(
    {
        *_PY_EXTS,
        *_JS_EXTS,
        ".pyi",
        ".go",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".rb",
        ".rs",
        ".php",
        ".cs",
        ".swift",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".m",
        ".mm",
        ".ex",
        ".exs",
        ".dart",
        ".lua",
        ".clj",
    }
)

# Test paths by directory segment or filename convention. Segment-anchored so
# `latest/` and `contest.py` stay code.
_TEST_RE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec)/"
    r"|(?:^|/)test_[^/]*\.py$|_test\.(?:py|go)$|_spec\.rb$"
    r"|\.(?:test|spec)\.[^/]+$"
)
_DOC_RE = re.compile(r"\.(?:md|mdx|rst|adoc)$|(?:^|/)docs?/", re.IGNORECASE)

# One rule per language family. TypeScript, TSX and JavaScript share a shape:
# static imports, `export … from`, and `require()` / dynamic `import()` calls.
_PY_RULE = (
    "id: py-imports\nlanguage: python\n"
    "rule: {any: [{kind: import_statement}, {kind: import_from_statement}]}\n"
)
_JS_RULE = (
    "id: {lang}-imports\nlanguage: {lang}\n"
    "rule:\n"
    "  any:\n"
    "    - {{kind: import_statement}}\n"
    "    - {{kind: export_statement, has: {{field: source, kind: string}}}}\n"
    '    - {{kind: call_expression, has: {{field: function, regex: "^(require|import)$"}}}}\n'
)
_SPECIFIER_RE = re.compile(r"""['"]([^'"]+)['"]""")
_FROM_RE = re.compile(r"^from\s+(\S+)\s+import\s+(.+)$", re.DOTALL)


def is_test_path(path: str) -> bool:
    """Whether *path* is a test file by directory or naming convention."""
    return bool(_TEST_RE.search(path))


def is_doc_path(path: str) -> bool:
    """Whether *path* is documentation: prose files, or anything under docs/."""
    return bool(_DOC_RE.search(path))


def _is_code(path: str) -> bool:
    return (
        Path(path).suffix.lower() in _CODE_EXTS and not is_test_path(path) and not is_doc_path(path)
    )


def _clean(path: str) -> str:
    """A path safe inside a Markdown code span — filenames are attacker-chosen."""
    return path.replace("`", "")


def _code(paths: Iterable[str]) -> str:
    return ", ".join(f"`{_clean(path)}`" for path in paths)


def default_scanner(binary: str, rules: str, root: Path) -> str | None:
    """Run one ast-grep scan over *root*; None unless it completed cleanly.

    Sandboxed like the static-analysis tools (scrubbed environment, hard
    timeout). Partial output from a timed-out or failed scan is discarded: a
    half-counted blast radius would look precise without being so.
    """
    try:
        proc = subprocess.run(
            [binary, "scan", "--inline-rules", rules, "--json=compact", "."],
            cwd=root,
            env=_scrubbed_env(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SCAN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _log.warning("risk import scan did not complete", exc_info=True)
        return None
    if proc.returncode != 0:
        _log.warning(
            "risk import scan failed",
            extra={"returncode": proc.returncode, "stderr": (proc.stderr or "")[:200]},
        )
        return None
    return proc.stdout or "[]"


def assess_risk(
    ctx: PRContext,
    cfg: ReviewConfig,
    workspace_root: Path | None,
    *,
    scanner: ImportScanner | None = None,
) -> RiskAssessment:
    """The change's risk verdict. Never raises: a failure is an unavailable verdict.

    *ctx* must be the whole pull request — assess before any incremental,
    triage or file-cap scoping narrows its diff.
    """
    try:
        return _assess(ctx, cfg, workspace_root, scanner or default_scanner)
    except Exception:  # noqa: BLE001 — a verdict failure must never fail the review
        _log.warning("risk assessment failed — reporting it unavailable", exc_info=True)
        return RiskAssessment(level=None)


def _assess(
    ctx: PRContext, cfg: ReviewConfig, root: Path | None, scanner: ImportScanner
) -> RiskAssessment:
    patches = split_by_file(ctx.diff, ctx.changed_files) if ctx.diff else []
    added = {path for path, patch in patches if _is_added(patch)}
    deleted = {path for path, patch in patches if _is_deleted(patch)}
    files = list(dict.fromkeys(ctx.changed_files))

    if files and all(is_test_path(f) or is_doc_path(f) for f in files):
        scope = RiskReason(factor="scope", key="scope", level="low", text="docs and tests only")
        return RiskAssessment(level="low", reasons=[scope])

    reasons: list[RiskReason] = []
    blast, unassessed = _blast_radius(files, added, deleted, cfg, root, scanner)
    reasons += blast
    reasons += _area_reasons(files)

    changed = changed_line_count(ctx.diff)
    if changed >= _LARGE_CHANGE_LINES:
        reasons.append(
            RiskReason(factor="size", key="size", level="medium", text=f"{changed} changed lines")
        )
    code_lines = sum(changed_line_count(patch) for path, patch in patches if _is_code(path))
    if code_lines >= _UNTESTED_CODE_LINES and not any(is_test_path(f) for f in files):
        reasons.append(
            RiskReason(
                factor="untested",
                key="untested",
                level="medium",
                text=f"{code_lines} changed lines of code with no test file changed",
            )
        )

    level = _level(reasons)
    if not reasons:
        reasons.append(RiskReason(factor="scope", key="scope", level="low", text="no risk signals"))
    reasons.sort(key=lambda r: (-_RANK[r.level], _FACTOR_ORDER[r.factor]))
    return RiskAssessment(level=level, reasons=reasons, unassessed=unassessed)


def _is_added(patch: str) -> bool:
    return "\n--- /dev/null" in patch or "\nnew file mode" in patch


def _is_deleted(patch: str) -> bool:
    return "\n+++ /dev/null" in patch or "\ndeleted file mode" in patch


def _level(reasons: Sequence[RiskReason]) -> RiskLevel:
    """The highest level any reason raises it to; two distinct highs are critical."""
    if len({r.key for r in reasons if r.level == "high"}) >= 2:
        return "critical"
    return max((r.level for r in reasons), key=_RANK.__getitem__, default="low")


def _area_reasons(files: Sequence[str]) -> list[RiskReason]:
    """One reason per High Impact area a changed code-or-config path implicates.

    Test and doc files are left out: a test named for auth, or a runbook, says
    nothing about what the change can break.
    """
    # Lazy: high_impact -> describe -> engine, and engine imports this module.
    from .high_impact import AREA_LABELS, path_signals

    relevant = [f for f in files if not is_test_path(f) and not is_doc_path(f)]
    reasons = []
    for area, paths in path_signals(relevant).items():
        level: RiskLevel = "high" if area in _HIGH_AREAS else "medium"
        label = AREA_LABELS[area].lower()
        reasons.append(
            RiskReason(
                factor="area", key=area, level=level, text=f"touches {label}: {_code(paths)}"
            )
        )
    return reasons


def _blast_radius(
    files: Sequence[str],
    added: set[str],
    deleted: set[str],
    cfg: ReviewConfig,
    root: Path | None,
    scanner: ImportScanner,
) -> tuple[list[RiskReason], list[str]]:
    """Reasons for widely imported or core files, and the files left unmeasured.

    A file the PR adds has no existing importers by definition, so it is
    measured (zero), never unassessed.
    """
    from .engine import passes_path_filters  # engine imports this module

    code = [f for f in files if _is_code(f) and f not in added]
    supported = [f for f in code if f.lower().endswith(_PY_EXTS + _JS_EXTS)]
    unassessed = [f for f in code if f not in supported]
    measurable: list[str] = []
    for path in supported:
        exists = root is not None and (root / path).is_file()
        if exists or (path in deleted and root is not None and root.is_dir()):
            measurable.append(path)
        else:
            unassessed.append(path)

    counts: dict[str, int] = {}
    if measurable and root is not None:
        scanned = _count_importers(measurable, set(files), root, scanner)
        if scanned is None:
            unassessed += measurable
        else:
            counts = scanned

    ranked: list[tuple[int, RiskReason]] = []
    for path in measurable:
        count = counts.get(path, 0)
        core = bool(cfg.risk.core_paths) and passes_path_filters(
            path, include=cfg.risk.core_paths, exclude=[]
        )
        level: RiskLevel | None = (
            "high"
            if core or count >= _HIGH_IMPORTERS
            else "medium"
            if count >= _MEDIUM_IMPORTERS
            else None
        )
        if level is None:
            continue
        if core:
            text = f"`{_clean(path)}` is a configured core path"
            text += f", imported by {count} file{'s' if count != 1 else ''}" if count else ""
        else:
            text = f"`{_clean(path)}` is imported by {count} files"
        reason = RiskReason(factor="blast_radius", key="blast_radius", level=level, text=text)
        ranked.append((count, reason))
    ranked.sort(key=lambda pair: -pair[0])
    return [reason for _, reason in ranked], sorted(unassessed, key=list(files).index)


def _count_importers(
    targets: Sequence[str], changed: set[str], root: Path, scanner: ImportScanner
) -> dict[str, int] | None:
    """Distinct importers per target, or None when the scan did not complete.

    Importers the PR itself changes are excluded, so the count is the code that
    already depends on a file — identical on a base or a head workspace.
    """
    binary = _find_binary()
    if binary is None:
        _log.warning("ast-grep not found — blast radius not assessed")
        return None
    py_targets = [t for t in targets if t.lower().endswith(_PY_EXTS)]
    js_targets = [t for t in targets if t.lower().endswith(_JS_EXTS)]
    rules = []
    if py_targets:
        rules.append(_PY_RULE)
    if js_targets:
        rules += [_JS_RULE.format(lang=lang) for lang in ("typescript", "tsx", "javascript")]
    stdout = scanner(binary, "---\n".join(rules), root)
    if stdout is None:
        return None

    py_index = _python_index(py_targets, root)
    js_index = {_js_key(t): t for t in js_targets}
    importers: dict[str, set[str]] = {t: set() for t in targets}
    for match in iter_matches(stdout):
        importer = str(match.get("file") or "").replace("\\", "/").removeprefix("./")
        text = str(match.get("text") or "")
        if not importer or importer in changed:
            continue
        if str(match.get("ruleId")) == "py-imports":
            hits = {py_index[key] for key in _python_keys(text, importer) if key in py_index}
        else:
            hits = {js_index[key] for key in _js_keys(text, importer) if key in js_index}
        for target in hits:
            if target != importer:
                importers[target].add(importer)
    return {target: len(found) for target, found in importers.items()}


def _python_index(targets: Sequence[str], root: Path) -> dict[str, str]:
    """Import keys that resolve to each Python target.

    Two kinds: its repo path without the extension (``src/pkg/core``, what a
    relative import resolves to), and every dotted name that can import it —
    one per source root, where a root is a directory that is not itself a
    package. ``src/pkg/core.py`` answers to ``pkg.core`` (``src`` has no
    ``__init__.py``) but not to a bare ``core`` (``src/pkg`` does).
    """
    index: dict[str, str] = {}
    for target in targets:
        stem = target[: -len(".py")]
        stem = stem.removesuffix("/__init__") if stem.endswith("/__init__") else stem
        index[stem] = target
        parts = stem.split("/")
        for cut in range(len(parts)):
            prefix = "/".join(parts[:cut])
            if prefix and (root / prefix / "__init__.py").is_file():
                continue
            index[".".join(parts[cut:])] = target
    return index


def _python_keys(text: str, importer: str) -> set[str]:
    """Every module an import statement could name, as index keys."""
    text = re.sub(r"#[^\n]*", "", text).replace("\\\n", " ")
    text = " ".join(text.replace("(", " ").replace(")", " ").split())
    match = _FROM_RE.match(text)
    if match is None:
        names = [part.split(" as ")[0].strip() for part in text.removeprefix("import ").split(",")]
        return {name for name in names if name}
    module, imported = match.groups()
    names = [part.split(" as ")[0].strip() for part in imported.split(",")]
    names = [name for name in names if name and name != "*"]
    if not module.startswith("."):
        return {module, *(f"{module}.{name}" for name in names)}
    dots = len(module) - len(module.lstrip("."))
    base = posixpath.dirname(importer)
    for _ in range(dots - 1):
        base = posixpath.dirname(base)
    rest = module.lstrip(".").replace(".", "/")
    package = posixpath.join(base, rest) if rest else base
    return {package, *(posixpath.join(package, name) for name in names)}


def _js_key(path: str) -> str:
    stem = path[: path.rfind(".")] if "." in posixpath.basename(path) else path
    return stem.removesuffix("/index")


def _js_keys(text: str, importer: str) -> set[str]:
    """The relative module an import names; bare specifiers are packages."""
    match = _SPECIFIER_RE.search(text)
    if match is None or not match.group(1).startswith("."):
        return set()
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(importer), match.group(1)))
    if resolved.lower().endswith(_JS_EXTS):
        resolved = resolved[: resolved.rfind(".")]
    return {resolved.removesuffix("/index")}


# --- rendering ------------------------------------------------------------------


def _caveat(risk: RiskAssessment) -> str:
    return f"blast radius not assessed: {_code(risk.unassessed)}"


def risk_segment(risk: RiskAssessment) -> str:
    """The summary line's risk segment: level plus its top reason."""
    if risk.level is None:
        return "risk unavailable"
    lines = [reason.text for reason in risk.reasons if reason.factor != "scope"]
    if risk.level == "low" and risk.unassessed:
        lines = [_caveat(risk)]
    elif not lines:
        lines = [reason.text for reason in risk.reasons]
    top = lines[0] if lines else ""
    more = f", +{len(lines) - 1} more" if len(lines) > 1 else ""
    return f"risk {risk.level} ({top}{more})" if top else f"risk {risk.level}"


def risk_marker(risk: RiskAssessment) -> str:
    """The hidden marker a gate reads; none when there is no verdict to read."""
    return "" if risk.level is None else f"<!-- lgtmaybe-risk:{risk.level} -->"


def render_risk_section(risk: RiskAssessment) -> str:
    """The overview's Risk of Change section: the level and every reason."""
    if risk.level is None:
        return (
            "### **Risk of Change: Unavailable**\n\n"
            "_The risk assessment could not run on this change._"
        )
    lines = [f"- {reason.text}" for reason in risk.reasons]
    if risk.unassessed:
        lines.append(f"- {_caveat(risk)}")
    return f"### **Risk of Change: {risk.level.capitalize()}**\n\n" + "\n".join(lines)
