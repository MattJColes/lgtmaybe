"""Tests for ast-grep cross-file symbol resolution (engine/astgrep.py).

Most tests inject a fake runner so they never shell out; the final test exercises
the real ast-grep binary (a core dependency) against a temp tree so the rule
shapes and JSON parsing are verified end to end.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lgtmaybe.engine.astgrep import (
    _rule_yaml as rule_yaml,
)
from lgtmaybe.engine.astgrep import (
    build_symbol_resolver,
)

_HAVE_BINARY = "ast-grep"  # any non-None string stands in for "binary present"


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


def test_resolver_uses_last_dotted_segment(tmp_path: Path) -> None:
    seen: list[str] = []

    def runner(binary: str, rule: str, root: Path) -> str:
        seen.append(rule)
        return "[]"

    resolve = build_symbol_resolver(
        lambda: tmp_path, runner=runner, find_binary=lambda: _HAVE_BINARY
    )
    assert resolve is not None
    resolve("ledger.already_applied")
    assert all("regex: ^already_applied$" in rule for rule in seen)


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
