"""Tests for ast-grep cross-file symbol resolution (engine/astgrep.py).

Most tests inject a fake runner so they never shell out; the final test exercises
the real ast-grep binary (a core dependency) against a temp tree so the rule
shapes and JSON parsing are verified end to end.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from lgtmaybe.engine.astgrep import (
    _LANGS,
    _parse_paths,
    block_kinds_for_path,
    build_symbol_resolver,
    rule_yaml,
)

_HAVE_BINARY = "ast-grep"  # any non-None string stands in for "binary present"

# The two pre-refactor tables, frozen verbatim (astgrep._LANG_KINDS and
# boundaries._LANG_DEFS). The shared table + shared builder must keep emitting the
# same rule for every language — same id, same language, same kind set.
_OLD_LANG_KINDS: dict[str, tuple[str, ...]] = {
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

_OLD_LANG_DEFS: dict[str, tuple[str, tuple[str, ...]]] = {
    ".py": ("python", ("function_definition", "class_definition")),
    ".js": ("javascript", ("function_declaration", "method_definition", "class_declaration")),
    ".jsx": ("javascript", ("function_declaration", "method_definition", "class_declaration")),
    ".ts": ("typescript", ("function_declaration", "method_definition", "class_declaration")),
    ".tsx": ("tsx", ("function_declaration", "method_definition", "class_declaration")),
    ".go": ("go", ("function_declaration", "method_declaration")),
    ".rs": ("rust", ("function_item", "impl_item", "trait_item")),
    ".java": ("java", ("method_declaration", "class_declaration")),
    ".rb": ("ruby", ("method", "singleton_method", "class", "module")),
}


def _old_kinds_flow(kinds: tuple[str, ...]) -> str:
    # The line that was character-for-character identical in both modules.
    return ", ".join(f"{{kind: {k}}}" for k in kinds)


def _old_symbol_rule(language: str, kinds: tuple[str, ...], symbol: str) -> str:
    return (
        "id: lgtmaybe-find-def\n"
        f"language: {language}\n"
        "rule:\n"
        "  all:\n"
        f"    - any: [{_old_kinds_flow(kinds)}]\n"
        f"    - has: {{field: name, regex: ^{symbol}$, stopBy: end}}\n"
    )


def _old_boundary_rule(language: str, kinds: tuple[str, ...]) -> str:
    return (
        f"id: lgtmaybe-boundaries\nlanguage: {language}\n"
        f"rule: {{any: [{_old_kinds_flow(kinds)}]}}\n"
    )


def _sorted_kinds(rule: str) -> str:
    """The rule with its ``any: [...]`` list sorted — that list is order-insensitive."""
    return re.sub(
        r"\[(\{kind: [^\]]+)\]",
        lambda m: "[" + ", ".join(sorted(m.group(1).split(", "))) + "]",
        rule,
    )


def test_symbol_rule_yaml_unchanged_by_the_shared_table() -> None:
    # Byte-for-byte the pre-refactor rule for all eight languages, modulo the order
    # of the order-insensitive `any:` list.
    for language, old_kinds in _OLD_LANG_KINDS.items():
        assert sorted(_LANGS[language].kinds) == sorted(old_kinds)
        assert _sorted_kinds(rule_yaml(language, _LANGS[language].kinds, "foo")) == _sorted_kinds(
            _old_symbol_rule(language, old_kinds, "foo")
        )


def test_boundary_rule_yaml_unchanged_by_the_shared_table() -> None:
    for ext, (language, old_kinds) in _OLD_LANG_DEFS.items():
        lang_defs = block_kinds_for_path(f"src/sample{ext}")
        assert lang_defs is not None
        assert lang_defs[0] == language
        assert sorted(lang_defs[1]) == sorted(old_kinds)
        assert _sorted_kinds(rule_yaml(*lang_defs)) == _sorted_kinds(
            _old_boundary_rule(language, old_kinds)
        )


def test_unsupported_extension_has_no_block_kinds() -> None:
    assert block_kinds_for_path("notes.txt") is None


def test_build_returns_none_without_binary() -> None:
    # No ast-grep installed → no resolver, so the caller keeps today's behaviour.
    assert build_symbol_resolver(lambda: Path("/x"), find_binary=lambda: None) is None


def test_rule_yaml_names_language_kinds_and_anchored_symbol() -> None:
    yaml = rule_yaml("python", ("function_definition", "class_definition"), "foo")
    assert "language: python" in yaml
    assert "kind: function_definition" in yaml
    assert "kind: class_definition" in yaml
    # Anchored so `foo` doesn't match `foobar`; identifier guard keeps it regex-safe.
    assert "regex: ^foo$" in yaml


def test_resolver_skips_non_identifier_symbols(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(binary: str, rule: str, root: Path) -> str:
        calls.append(rule)
        return "[]"

    resolve = build_symbol_resolver(
        lambda: tmp_path, runner=runner, find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    assert resolve("a/b.py") == []  # path-shaped — the path fetcher's job
    assert resolve("models.py") == []  # filename-shaped — not a `module.symbol`
    assert resolve("123") == []
    assert resolve("") == []
    assert resolve("has space") == []
    assert calls == []  # never shelled out for non-identifiers


def test_resolver_parses_and_dedupes_paths(tmp_path: Path) -> None:
    target = tmp_path / "ledger.py"
    target.write_text("def already_applied():\n    pass\n")
    payload = json.dumps([{"file": str(target)}, {"file": str(target)}])

    def runner(binary: str, rule: str, root: Path) -> str:
        return payload if "language: python\n" in rule else "[]"

    resolve = build_symbol_resolver(
        lambda: tmp_path, runner=runner, find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    # Returned as a repo-relative path, de-duplicated across the match array.
    assert resolve("already_applied") == ["ledger.py"]


def test_parse_paths_emits_posix_separators(tmp_path: Path) -> None:
    output = json.dumps([{"file": str(tmp_path / "src" / "app.py")}])

    assert _parse_paths(output, tmp_path.resolve()) == ["src/app.py"]


def test_only_languages_present_in_the_tree_are_scanned(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    scanned: list[str] = []

    def runner(binary: str, rule: str, root: Path) -> str:
        scanned.append(rule.split("language: ", 1)[1].split("\n", 1)[0])
        return "[]"

    resolve = build_symbol_resolver(
        lambda: tmp_path, runner=runner, find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    # Defined nowhere — the common case, since this runs only after the fetch failed.
    assert resolve("defined_nowhere") == []
    # One subprocess for the one language present, not one per supported language.
    assert scanned == ["python"]


def test_resolver_uses_last_dotted_segment(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    seen: list[str] = []

    def runner(binary: str, rule: str, root: Path) -> str:
        seen.append(rule)
        return "[]"

    resolve = build_symbol_resolver(
        lambda: tmp_path, runner=runner, find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    resolve("ledger.already_applied")
    assert seen and all("regex: ^already_applied$" in rule for rule in seen)


def test_resolver_caps_candidates(tmp_path: Path) -> None:
    files = [tmp_path / f"f{i}.py" for i in range(5)]
    for f in files:
        f.write_text("x = 1\n")
    payload = json.dumps([{"file": str(f)} for f in files])

    resolve = build_symbol_resolver(
        lambda: tmp_path,
        runner=lambda *_: payload,
        find_binary=lambda: _HAVE_BINARY,
    )
    assert resolve is not None
    assert len(resolve("x")) == 3


def test_resolver_returns_empty_when_no_corpus_root() -> None:
    resolve = build_symbol_resolver(
        lambda: None, runner=lambda *_: "[]", find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    assert resolve("anything") == []


def test_corpus_root_resolved_at_most_once(tmp_path: Path) -> None:
    calls = {"n": 0}

    def get_root() -> Path:
        calls["n"] += 1
        return tmp_path

    resolve = build_symbol_resolver(
        get_root, runner=lambda *_: "[]", find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    resolve("a")
    resolve("b")
    assert calls["n"] == 1  # the (potentially expensive) provision is cached


def test_malformed_runner_output_is_tolerated(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    resolve = build_symbol_resolver(
        lambda: tmp_path, runner=lambda *_: "not json", find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    assert resolve("foo") == []


@pytest.mark.skipif(shutil.which("ast-grep") is None, reason="ast-grep binary not installed")
def test_real_ast_grep_finds_definitions_across_languages(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "ledger.py").write_text(
        "async def already_applied(run_id) -> bool:\n    return run_id in _seen\n"
    )
    (tmp_path / "app.ts").write_text(
        "export function markApplied(id: string): Promise<void> { return; }\n"
    )

    resolve = build_symbol_resolver(lambda: tmp_path)
    assert resolve is not None
    assert resolve("already_applied") == ["pkg/ledger.py"]
    assert resolve("markApplied") == ["app.ts"]
    assert resolve("does_not_exist_anywhere") == []
