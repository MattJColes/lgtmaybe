"""ast-grep symbol resolution for cross-file reflection deferrals.

When the reflection auditor (``engine/reflect.py``) would drop a finding ONLY
because it cannot see a SYMBOL the finding depends on — a function, class, type,
or const defined in a file the diff doesn't include — it defers by naming that
symbol. The existing fetcher (``engine/retrieve.py``) can only resolve a *path*;
a bare symbol name resolves to nothing. This module closes that gap: it
structurally locates the file(s) that DEFINE the symbol so the existing
read-only fetcher can pull the right file and the auditor can re-judge with the
real definition in front of it.

ast-grep only *parses* the corpus — it never executes it — so running it over the
local worktree (the CLI's own checkout) or a checkout of the trusted BASE branch
(the GitHub path) stays inside the fork-safety model: the corpus is never PR head
code, and parsing is not execution. Resolution is best-effort end to end: a
missing binary, an absent corpus root, a non-identifier symbol, an unsupported
language, or any ast-grep failure yields ``[]`` and the caller falls back to its
existing path-only behaviour.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from lgtmaybe.core.logging import get_logger

_log = get_logger(__name__)

# A symbol name -> candidate repo-relative file paths that define it.
SymbolResolver = Callable[[str], list[str]]
# Lazily yields the corpus root to search (the worktree, or a base checkout), or
# None when none is available. Called once; the result is cached by the resolver,
# so a costly provision (e.g. cloning the base branch) happens at most once per
# review and only when a symbol deferral actually occurs.
RootProvider = Callable[[], "Path | None"]
# Abstracts the ast-grep subprocess: (binary, rule_yaml, root) -> stdout. Injected
# so tests don't shell out. Returns "" on any failure.
AstGrepRunner = Callable[[str, str, Path], str]

# A structural scan of one repo is sub-second (validated at ~20ms over this repo);
# the timeout only caps a pathological run, never a normal one — so it is set well
# above what a large monorepo needs rather than near the measured cost.
_SCAN_TIMEOUT = 60
_MAX_CANDIDATES = 3
# Only resolve identifier-shaped names. This both filters out path-shaped `needs`
# (handled by the path fetcher) and keeps the symbol safe to drop into the rule's
# ``regex: ^<name>$`` — an identifier carries no regex or YAML metacharacters.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _LangSpec(NamedTuple):
    """One ast-grep language: the files it owns and its definition node kinds.

    ``block`` kinds OPEN a body worth padding a hunk back to (functions, methods,
    classes) — ``engine/boundaries.py`` scans those alone, since a
    ``variable_declarator`` boundary would be noise. Symbol resolution scans
    :attr:`kinds`, the block kinds plus the rest. One table, one blockness flag —
    so adding a language can no longer land in half of it.
    """

    exts: tuple[str, ...]
    block: tuple[str, ...]
    other: tuple[str, ...] = ()

    @property
    def kinds(self) -> tuple[str, ...]:
        return self.block + self.other


# ast-grep language name -> its file extensions and the tree-sitter definition
# node kinds whose ``name`` field we match. Validated against ast-grep 0.44. C/C++
# are intentionally omitted: their function names nest inside declarators that
# ``has: {field: name}`` can't bind, and a broken matcher is worse than none.
# ``.jsx`` maps to ``javascript`` and ``.tsx`` to ``tsx`` by ast-grep's own
# extension table — we just supply the matching language name so its file
# selection lines up.
_LANGS: dict[str, _LangSpec] = {
    "python": _LangSpec((".py",), ("function_definition", "class_definition")),
    "javascript": _LangSpec(
        (".js", ".jsx", ".mjs", ".cjs"),
        ("function_declaration", "method_definition", "class_declaration"),
        ("variable_declarator",),
    ),
    "typescript": _LangSpec(
        (".ts", ".mts", ".cts"),
        ("function_declaration", "method_definition", "class_declaration"),
        (
            "variable_declarator",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        ),
    ),
    "tsx": _LangSpec(
        (".tsx",),
        ("function_declaration", "method_definition", "class_declaration"),
        (
            "variable_declarator",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        ),
    ),
    "go": _LangSpec(
        (".go",),
        ("function_declaration", "method_declaration"),
        ("type_spec", "const_spec"),
    ),
    "java": _LangSpec(
        (".java",),
        ("method_declaration", "class_declaration"),
        ("interface_declaration", "enum_declaration", "record_declaration"),
    ),
    "rust": _LangSpec(
        (".rs",),
        ("function_item", "impl_item", "trait_item"),
        (
            "struct_item",
            "enum_item",
            "const_item",
            "static_item",
            "type_item",
            "mod_item",
            "macro_definition",
        ),
    ),
    "ruby": _LangSpec((".rb",), ("method", "singleton_method", "class", "module")),
}

# Extension -> (language, block kinds), the lookup boundary detection needs.
_BY_EXT: dict[str, tuple[str, tuple[str, ...]]] = {
    ext: (language, spec.block) for language, spec in _LANGS.items() for ext in spec.exts
}


def block_kinds_for_path(path: str) -> tuple[str, tuple[str, ...]] | None:
    """``(ast-grep language, body-opening kinds)`` for *path*, or None if unsupported."""
    return _BY_EXT.get(Path(path).suffix.lower())


def _symbol_name(raw: str) -> str | None:
    """The definition name to search for, or None if *raw* isn't a bare symbol.

    Rejects path-shaped (`a/b.py`) and filename-shaped (`models.py`) inputs — those
    are the path fetcher's job — and accepts a dotted reference (`mod.func`,
    `self.method`) by taking its final segment, the name ast-grep can match.
    """
    s = raw.strip()
    if not s or "/" in s or "\\" in s or Path(s).suffix.lower() in _BY_EXT:
        return None
    name = s.rsplit(".", 1)[-1]
    return name if _IDENT.match(name) else None


def _find_binary() -> str | None:
    """Locate the ast-grep executable (shipped by the ``ast-grep-cli`` core dep)."""
    return shutil.which("ast-grep")


def rule_yaml(language: str, kinds: Sequence[str], symbol: str | None = None) -> str:
    """An inline ast-grep rule matching any of *kinds* in *language*.

    With *symbol*, the match is narrowed to a definition whose ``name`` field
    equals it (searched through nested declarators via ``stopBy: end``) — symbol
    resolution's rule. *symbol* is identifier-validated by the caller, so it is
    safe inside the ``^...$`` regex with no escaping. Without it, the bare
    ``any:`` rule boundary detection uses.
    """
    kinds_flow = ", ".join(f"{{kind: {k}}}" for k in kinds)
    if symbol is None:
        return f"id: lgtmaybe-boundaries\nlanguage: {language}\nrule: {{any: [{kinds_flow}]}}\n"
    return (
        "id: lgtmaybe-find-def\n"
        f"language: {language}\n"
        "rule:\n"
        "  all:\n"
        f"    - any: [{kinds_flow}]\n"
        f"    - has: {{field: name, regex: ^{symbol}$, stopBy: end}}\n"
    )


def _present_languages(root: Path) -> tuple[str, ...]:
    """The languages actually present under *root*, by file extension.

    Scanning a pure-Python tree for a Ruby ``singleton_method`` is guaranteed-zero
    work; one filesystem walk (cached with the corpus root) replaces up to eight
    repo-wide ``ast-grep scan`` subprocesses per unresolved symbol.
    """
    try:
        exts = {path.suffix.lower() for path in root.rglob("*")}
    except OSError:  # unreadable corpus — best-effort, scan nothing
        return ()
    return tuple(name for name, spec in _LANGS.items() if not exts.isdisjoint(spec.exts))


def _default_runner(binary: str, rule_yaml: str, target: Path) -> str:
    """Run ``ast-grep scan`` with an inline rule, returning stdout (or "").

    Exit codes are ignored on purpose: a no-match scan exits 0 with ``[]`` and a
    rule with a kind invalid for the language exits non-zero — either way the JSON
    on stdout (or its absence) is what we parse, so a failure degrades to "no
    candidates" rather than raising.
    """
    try:
        proc = subprocess.run(
            [binary, "scan", "--inline-rules", rule_yaml, "--json=compact", str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SCAN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout or ""


def iter_matches(stdout: str) -> Iterator[dict[str, Any]]:
    """The dict entries of ast-grep's compact JSON match array.

    Tolerates empty or malformed output (yields nothing) — shared guard
    scaffolding for every ast-grep output parser.
    """
    try:
        data = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return
    if not isinstance(data, list):
        return
    for match in data:
        if isinstance(match, dict):
            yield match


def _parse_paths(stdout: str, root: Path) -> list[str]:
    """Repo-relative file paths from ast-grep's compact JSON match array.

    A path that doesn't sit under *root* (shouldn't happen — we scan *root*) is
    skipped rather than leaked as an absolute path.
    """
    out: list[str] = []
    for match in iter_matches(stdout):
        file = match.get("file")
        if not isinstance(file, str) or not file:
            continue
        try:
            rel = Path(file).resolve().relative_to(root)
        except ValueError:
            continue
        out.append(rel.as_posix())
    return out


def build_symbol_resolver(
    get_root: RootProvider,
    *,
    runner: AstGrepRunner | None = None,
    find_binary: Callable[[], str | None] = _find_binary,
) -> SymbolResolver | None:
    """A resolver mapping a symbol name to the file(s) defining it — or None.

    Returns None when ast-grep isn't installed; the caller then leaves the
    reflection pass on its existing path-only fetch (no behaviour change).
    Otherwise returns a callable that, given a symbol the auditor deferred on,
    structurally searches the corpus (``get_root()``, resolved and cached on first
    use) for its definition and returns up to :data:`_MAX_CANDIDATES`
    repo-relative paths. Only the languages the corpus actually contains are
    scanned, so an unresolved symbol costs one subprocess per present language
    rather than one per supported language.

    Best-effort throughout: a non-identifier symbol, an absent corpus, an
    unsupported language, or any ast-grep failure yields [] so the deferral falls
    back to its existing behaviour instead of erroring.
    """
    binary = find_binary()
    if binary is None:
        _log.info("ast-grep not found — cross-file symbol resolution disabled")
        return None
    run = runner or _default_runner

    @functools.cache
    def _corpus() -> tuple[Path, tuple[str, ...]] | None:
        """The resolved corpus root and the languages present in it, or None."""
        root = get_root()
        if root is None:
            return None
        root = root.resolve()
        return root, _present_languages(root)

    def resolve(symbol: str) -> list[str]:
        name = _symbol_name(symbol)
        if name is None:
            return []
        corpus = _corpus()
        if corpus is None:
            return []
        root, languages = corpus
        found: list[str] = []
        seen: set[str] = set()
        for language in languages:
            if len(found) >= _MAX_CANDIDATES:
                break
            stdout = run(binary, rule_yaml(language, _LANGS[language].kinds, name), root)
            for rel in _parse_paths(stdout, root):
                if rel in seen:
                    continue
                seen.add(rel)
                found.append(rel)
                if len(found) >= _MAX_CANDIDATES:
                    break
        if found:
            _log.info(
                "ast-grep resolved deferred symbol",
                extra={"symbol": name, "files": found},
            )
        return found

    return resolve
