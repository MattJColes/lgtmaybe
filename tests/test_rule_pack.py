"""The bundled semgrep rules: shipped as package data, so guard their shape.

We ship our own MIT rules rather than vendoring an upstream pack, because the
widely-used semgrep-rules / opengrep-rules collections are LGPL-2.1 **plus a
Commons Clause** — not an open-source licence, and not something an MIT wheel
published to PyPI, Homebrew and GHCR can carry without misrepresenting itself.

These tests exist because the pack is data, not code: nothing else would notice
a rule file that stopped parsing, lost its licence, or quietly emptied out.
"""

from __future__ import annotations

import yaml

from lgtmaybe.engine.static_analysis import bundled_semgrep_rules

# The pack rides in the PyPI wheel, the Homebrew venv, and the one-file Windows
# executable, so its size is a shipping cost on every distribution channel.
MAX_PACK_BYTES = 1_500_000
MIN_RULES = 10


def _rule_files() -> list:
    return sorted(bundled_semgrep_rules().glob("*.yml"))


def test_the_pack_ships_with_the_package() -> None:
    assert bundled_semgrep_rules().is_dir()
    assert _rule_files(), "no rule files found — the pack did not ship"


def test_every_rule_file_parses_and_declares_the_required_fields() -> None:
    for path in _rule_files():
        rules = yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]
        assert rules, f"{path.name} declares no rules"
        for rule in rules:
            for field in ("id", "languages", "severity", "message"):
                assert rule.get(field), f"{path.name}: {rule.get('id')!r} lacks {field}"


def test_rule_ids_are_unique_and_namespaced() -> None:
    ids = [
        rule["id"]
        for path in _rule_files()
        for rule in yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]
    ]
    assert len(ids) == len(set(ids)), "duplicate rule ids"
    assert all(i.startswith("lgtmaybe-") for i in ids), "rule ids must be namespaced"
    assert len(ids) >= MIN_RULES, f"only {len(ids)} rules — did a refresh empty the pack?"


def test_no_rule_reaches_the_network() -> None:
    """The sandbox forbids it, and a rule that fetched would fail silently."""
    for path in _rule_files():
        text = path.read_text(encoding="utf-8")
        for marker in ("http://", "semgrep.dev", "--config auto"):
            assert marker not in text, f"{path.name} references {marker}"


def test_the_pack_carries_its_own_licence() -> None:
    licence = bundled_semgrep_rules() / "LICENSE"
    assert licence.is_file(), "the rule pack must ship its licence"
    assert "MIT License" in licence.read_text(encoding="utf-8")


def test_the_pack_stays_within_its_size_budget() -> None:
    total = sum(p.stat().st_size for p in bundled_semgrep_rules().rglob("*"))
    assert total <= MAX_PACK_BYTES, f"rule pack is {total} bytes"
