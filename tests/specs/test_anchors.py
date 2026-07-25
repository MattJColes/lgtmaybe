"""Hygiene gate for the living specs (openspec/specs/) and their ast-grep anchors.

Every requirement section in a spec carries an anchor id (an invisible HTML
comment) bound to code by ast-grep rules in the capability's co-located
``anchors.yml`` sidecar. This suite enforces the convention deterministically:

- spec.md anchor ids and anchors.yml rule ids are a bijection, per capability
- every rule resolves to EXACTLY one place in an anchored code root
  (0 = dangling, >1 = too loose)
- every spec.md has the OpenSpec shape (Purpose / Requirements / Scenario, SHALL)
- requirement sections stay under the 40-line soft cap (split, don't append)

The non-deterministic half of the convention — "did anchored code change while
its spec section sat still?" — lives in scripts/check_spec_drift.py and warns
(never fails) on PRs via .github/workflows/spec-drift.yml.
"""

import re
import sys
from pathlib import Path

import pytest

from lgtmaybe.engine.astgrep import _find_binary

REPO_ROOT = Path(__file__).parent.parent.parent
SPECS_DIR = REPO_ROOT / "openspec" / "specs"

# Allow importing the drift script without it being a package (the
# tests/docs/test_reference_fresh.py pattern).
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_spec_drift import (  # noqa: E402
    extract_sections,
    load_anchors,
    run_scan,
    to_inline_rules,
)

SECTION_LINE_CAP = 40
ANCHOR_RE = re.compile(r"^<!-- anchor: (?P<id>[a-z0-9.-]+) -->$")


def _spec_files() -> list[Path]:
    return sorted(SPECS_DIR.glob("*/spec.md"))


def _spec_anchor_ids(spec: Path) -> list[str]:
    ids = []
    for line in spec.read_text(encoding="utf-8").splitlines():
        m = ANCHOR_RE.match(line.strip())
        if m:
            ids.append(m.group("id"))
    return ids


def test_living_specs_exist() -> None:
    assert _spec_files(), (
        f"no living specs found under {SPECS_DIR} — "
        "expected openspec/specs/<capability>/spec.md files"
    )


def test_anchor_ids_are_a_bijection_per_capability() -> None:
    """Every spec anchor id has a sidecar rule in the same capability dir, and
    vice versa; ids are globally unique."""
    rules = load_anchors(REPO_ROOT)
    seen_spec_ids: set[str] = set()
    for spec in _spec_files():
        spec_ids = _spec_anchor_ids(spec)
        assert len(spec_ids) == len(set(spec_ids)), f"duplicate anchor ids in {spec}"
        duplicates = seen_spec_ids & set(spec_ids)
        assert not duplicates, f"anchor ids reused across specs: {sorted(duplicates)}"
        seen_spec_ids |= set(spec_ids)

        sidecar = spec.parent / "anchors.yml"
        assert sidecar.exists(), f"{spec.parent.name} has a spec.md but no anchors.yml"
        sidecar_rel = sidecar.relative_to(REPO_ROOT).as_posix()
        sidecar_ids = {r.anchor_id for r in rules if r.sidecar == sidecar_rel}
        assert set(spec_ids) == sidecar_ids, (
            f"{spec.parent.name}: spec.md and anchors.yml disagree — "
            f"only in spec.md: {sorted(set(spec_ids) - sidecar_ids)}, "
            f"only in anchors.yml: {sorted(sidecar_ids - set(spec_ids))}"
        )

    all_sidecar_ids = {r.anchor_id for r in rules}
    assert all_sidecar_ids == seen_spec_ids


def test_every_rule_resolves_to_exactly_one_place() -> None:
    """The anchor-hygiene invariant: 0 matches = dangling, >1 = too loose."""
    binary = _find_binary()
    assert binary is not None, "ast-grep binary missing — it ships with the ast-grep-cli core dep"
    rules = load_anchors(REPO_ROOT)
    assert rules, "no anchor rules found"
    matches = run_scan(binary, to_inline_rules(rules), REPO_ROOT)
    problems = []
    for rule in rules:
        hits = matches.get(rule.rule_id, [])
        if len(hits) != 1:
            where = ", ".join(f"{m.file}:{m.start_line}" for m in hits) or "nothing"
            problems.append(f"{rule.rule_id} (from {rule.sidecar}) matched {where}")
    assert not problems, (
        "each anchor rule must resolve to exactly one place — "
        "tighten with files:/inside: or fix the dangling rule:\n  " + "\n  ".join(problems)
    )


def test_specs_have_openspec_shape() -> None:
    for spec in _spec_files():
        text = spec.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert "## Purpose" in text, f"{spec}: missing '## Purpose'"
        assert "## Requirements" in text, f"{spec}: missing '## Requirements'"
        sections = extract_sections(text, spec.relative_to(REPO_ROOT).as_posix())
        headings = [i for i, line in enumerate(lines) if line.startswith("### Requirement:")]
        assert headings, f"{spec}: no '### Requirement:' sections"
        assert len(sections) == len(headings), (
            f"{spec}: every '### Requirement:' section must contain its "
            f"'<!-- anchor: ... -->' comment — on its own line right after the opening "
            f"paragraph ({len(headings)} headings, {len(sections)} anchored)"
        )
        for section in sections.values():
            body = "\n".join(lines[section.heading_line - 1 : section.end_line])
            assert "#### Scenario:" in body, (
                f"{spec}: requirement '{section.anchor_id}' has no '#### Scenario:' block"
            )
            # `openspec validate` only reads the FIRST line of the requirement
            # text for the keyword check, so SHALL/MUST has to land there.
            first_prose = next(
                (ln for ln in lines[section.heading_line : section.end_line] if ln.strip()),
                "",
            )
            assert "SHALL" in first_prose or "MUST" in first_prose, (
                f"{spec}: requirement '{section.anchor_id}' must state SHALL/MUST on the "
                f"first line of its text (openspec validate reads only that line)"
            )


def test_requirement_sections_respect_the_size_cap() -> None:
    """Soft cap from the convention: a section that outgrows ~40 lines should be
    split or summarised at write time, not appended to forever."""
    for spec in _spec_files():
        text = spec.read_text(encoding="utf-8")
        for section in extract_sections(text, spec.name).values():
            size = section.end_line - section.heading_line + 1
            if size > SECTION_LINE_CAP:
                pytest.fail(
                    f"{spec}: section '{section.anchor_id}' is {size} lines "
                    f"(cap {SECTION_LINE_CAP}) — split it or tighten the prose"
                )
