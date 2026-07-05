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
from collections.abc import Callable
from pathlib import Path

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
# the timeout only caps a pathological run, never a normal one.
_SCAN_TIMEOUT = 20
_DEFAULT_MAX_CANDIDATES = 3
# Only resolve identifier-shaped names. This both filters out path-shaped `needs`
# (handled by the path fetcher) and keeps the symbol safe to drop into the rule's
# ``regex: ^<name>$`` — an identifier carries no regex or YAML metacharacters.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Source-file extensions (no dot). A `need` like ``models.py`` is a filename the
# path fetcher handles — not a ``module.symbol`` reference — so it must not be
# mistaken for a symbol whose name is ``py``.
_SOURCE_EXTS = frozenset(
    {"py", "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts", "go", "java", "rs", "rb"}
)


def _symbol_name(raw: str) -> str | None:
    """The definition name to search for, or None if *raw* isn't a bare symbol.

    Rejects path-shaped (`a/b.py`) and filename-shaped (`models.py`) inputs — those
    are the path fetcher's job — and accepts a dotted reference (`mod.func`,
    `self.method`) by taking its final segment, the name ast-grep can match.
    """
    s = raw.strip()
    if not s or "/" in s or "\\" in s:
        return None
    name = s.rsplit(".", 1)[-1]
    if name in _SOURCE_EXTS:
        return None
    return name if _IDENT.match(name) else None


# ast-grep language name -> the tree-sitter definition node kinds whose ``name``
# field we match. Validated against ast-grep 0.44. C/C++ are intentionally omitted:
# their function names nest inside declarators that ``has: {field: name}`` can't
# bind, and a broken matcher is worse than none. ``.jsx`` maps to ``javascript``
# and ``.tsx`` to ``tsx`` by ast-grep's own extension table — we just supply the
# matching language name so its file selection lines up.
_LANG_KINDS: dict[str, tuple[str, ...]] = {
    "python": ("function_definition", "class_definition"),
    "javascript": (
        "function_declaration",
        "class_declaration",
        "method_definition",
        "variable_declarator",
    ),
    "typescript": (
        "function_declaration",
        "class_declaration",
        "method_definition",
        "variable_declarator",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    ),
    "tsx": (
        "function_declaration",
        "class_declaration",
        "method_definition",
        "variable_declarator",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    ),
    "go": ("function_declaration", "method_declaration", "type_spec", "const_spec"),
    "java": (
        "method_declaration",
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    ),
    "rust": (
        "function_item",
        "struct_item",
        "enum_item",
        "trait_item",
        "impl_item",
        "const_item",
        "static_item",
        "type_item",
        "mod_item",
        "macro_definition",
    ),
    "ruby": ("method", "singleton_method", "class", "module"),
}


def _find_binary() -> str | None:
    """Locate the ast-grep executable (shipped by the ``ast-grep-cli`` core dep)."""
    return shutil.which("ast-grep")


def ast_grep_available(*, find_binary: Callable[[], str | None] = _find_binary) -> bool:
    """True when the ast-grep binary is on PATH."""
    return find_binary() is not None


def _rule_yaml(language: str, kinds: tuple[str, ...], symbol: str) -> str:
    """An inline ast-grep rule: a definition node of *language* named *symbol*.

    Matches any of *kinds* whose ``name`` field equals *symbol* (searched through
    nested declarators via ``stopBy: end``). *symbol* is identifier-validated by
    the caller, so it is safe inside the ``^...$`` regex with no escaping.
    """
    kinds_flow = ", ".join(f"{{kind: {k}}}" for k in kinds)
    return (
        "id: lgtmaybe-find-def\n"
        f"language: {language}\n"
        "rule:\n"
        "  all:\n"
        f"    - any: [{kinds_flow}]\n"
        f"    - has: {{field: name, regex: ^{symbol}$, stopBy: end}}\n"
    )


def _default_runner(binary: str, rule_yaml: str, root: Path) -> str:
    """Run ``ast-grep scan`` with an inline rule, returning stdout (or "").

    Exit codes are ignored on purpose: a no-match scan exits 0 with ``[]`` and a
    rule with a kind invalid for the language exits non-zero — either way the JSON
    on stdout (or its absence) is what we parse, so a failure degrades to "no
    candidates" rather than raising.
    """
    try:
        proc = subprocess.run(
            [binary, "scan", "--inline-rules", rule_yaml, "--json=compact", str(root)],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout or ""


def _parse_paths(stdout: str, root: Path) -> list[str]:
    """Repo-relative file paths from ast-grep's compact JSON match array.

    Tolerates empty or malformed output (returns []). A path that doesn't sit under
    *root* (shouldn't happen — we scan *root*) is skipped rather than leaked as an
    absolute path.
    """
    try:
        data = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for match in data:
        if not isinstance(match, dict):
            continue
        file = match.get("file")
        if not isinstance(file, str) or not file:
            continue
        try:
            rel = Path(file).resolve().relative_to(root)
        except ValueError:
            continue
        out.append(str(rel))
    return out


def build_symbol_resolver(
    get_root: RootProvider,
    *,
    runner: AstGrepRunner | None = None,
    find_binary: Callable[[], str | None] = _find_binary,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
) -> SymbolResolver | None:
    """A resolver mapping a symbol name to the file(s) defining it — or None.

    Returns None when ast-grep isn't installed; the caller then leaves the
    reflection pass on its existing path-only fetch (no behaviour change).
    Otherwise returns a callable that, given a symbol the auditor deferred on,
    structurally searches the corpus (``get_root()``, resolved and cached on first
    use) for its definition and returns up to *max_candidates* repo-relative paths.

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
    def _root() -> Path | None:
        root = get_root()
        return root.resolve() if root is not None else None

    def resolve(symbol: str) -> list[str]:
        name = _symbol_name(symbol)
        if name is None:
            return []
        root = _root()
        if root is None:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for language, kinds in _LANG_KINDS.items():
            if len(found) >= max_candidates:
                break
            stdout = run(binary, _rule_yaml(language, kinds, name), root)
            for rel in _parse_paths(stdout, root):
                if rel in seen:
                    continue
                seen.add(rel)
                found.append(rel)
                if len(found) >= max_candidates:
                    break
        if found:
            _log.info(
                "ast-grep resolved deferred symbol",
                extra={"symbol": name, "files": found},
            )
        return found

    return resolve
