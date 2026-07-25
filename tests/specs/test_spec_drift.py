"""Unit tests for the pure functions in scripts/check_spec_drift.py.

The drift gate itself is warnings-only CI plumbing; these tests pin down the
deterministic core: sidecar parsing, rule translation, spec-section extraction,
changed-line intersection, and the DANGLING/DRIFT classification — all with
inline fixtures, no subprocess and no git.
"""

import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_spec_drift import (  # noqa: E402
    AnchorRule,
    Match,
    SpecSection,
    changed_right_lines,
    classify,
    extract_sections,
    load_anchors,
    parse_scan_output,
    to_inline_rules,
)

SIDECAR = textwrap.dedent(
    """
    one.single:
      rule:
        kind: function_definition
        has: { field: name, regex: '^lonely$' }
      files: [src/pkg/**/*.py]

    two.listed:
      - rule: { kind: class_definition, has: { field: name, regex: '^First$' } }
        files: [src/pkg/a/**]
      - rule: { kind: class_definition, has: { field: name, regex: '^Second$' } }
        files: [src/pkg/b/**]
    """
)

SPEC = textwrap.dedent(
    """\
    # thing Specification

    ## Purpose
    Words.

    ## Requirements

    ### Requirement: Single anchored thing

    The thing SHALL be single.
    <!-- anchor: one.single -->

    #### Scenario: it is single
    - **WHEN** asked
    - **THEN** single

    ### Requirement: Listed thing

    The thing SHALL be listed.
    <!-- anchor: two.listed -->

    #### Scenario: it is listed
    - **WHEN** asked
    - **THEN** listed
    """
)


def _write_capability(root: Path, name: str, spec: str, sidecar: str) -> None:
    cap = root / "openspec" / "specs" / name
    cap.mkdir(parents=True)
    (cap / "spec.md").write_text(spec, encoding="utf-8")
    (cap / "anchors.yml").write_text(sidecar, encoding="utf-8")


def test_load_anchors_normalises_single_and_list_forms(tmp_path: Path) -> None:
    _write_capability(tmp_path, "thing", SPEC, SIDECAR)
    rules = load_anchors(tmp_path)
    assert [r.rule_id for r in rules] == ["one.single__0", "two.listed__0", "two.listed__1"]
    assert all(r.sidecar == "openspec/specs/thing/anchors.yml" for r in rules)
    single = rules[0]
    assert single.anchor_id == "one.single"
    assert single.files == ["src/pkg/**/*.py"]
    assert single.rule["kind"] == "function_definition"


def test_to_inline_rules_emits_one_scan_document_per_rule() -> None:
    rules = [
        AnchorRule(
            anchor_id="a.b",
            rule_id="a.b__0",
            rule={"kind": "function_definition"},
            files=["src/**"],
            sidecar="openspec/specs/x/anchors.yml",
        ),
        AnchorRule(
            anchor_id="c.d",
            rule_id="c.d__0",
            rule={"kind": "class_definition"},
            files=["src/**"],
            sidecar="openspec/specs/y/anchors.yml",
            language="yaml",
        ),
    ]
    text = to_inline_rules(rules)
    docs = [d for d in text.split("\n---\n") if d.strip()]
    assert len(docs) == 2
    assert "id: a.b__0" in docs[0]
    assert "language: python" in docs[0]
    assert "language: yaml" in docs[1]
    assert "kind: class_definition" in docs[1]


def test_extract_sections_finds_ranges_and_the_last_section() -> None:
    sections = extract_sections(SPEC, "openspec/specs/thing/spec.md")
    assert set(sections) == {"one.single", "two.listed"}
    single = sections["one.single"]
    assert single == SpecSection(
        anchor_id="one.single",
        spec_path="openspec/specs/thing/spec.md",
        heading_line=8,
        anchor_line=11,
        end_line=16,
    )
    listed = sections["two.listed"]
    assert listed.heading_line == 17
    assert listed.end_line == len(SPEC.splitlines())  # last section runs to EOF


def test_parse_scan_output_converts_to_one_based_lines() -> None:
    payload = (
        '[{"ruleId": "a.b__0", "file": "src/x.py",'
        ' "range": {"start": {"line": 4}, "end": {"line": 9}}}]'
    )
    matches = parse_scan_output(payload)
    assert matches == {"a.b__0": [Match(file="src/x.py", start_line=5, end_line=10)]}


def test_changed_right_lines_reads_a_unified_diff() -> None:
    diff = textwrap.dedent(
        """\
        diff --git a/src/x.py b/src/x.py
        --- a/src/x.py
        +++ b/src/x.py
        @@ -1,3 +1,4 @@
         def f():
        -    return 1
        +    x = 2
        +    return x
        """
    )
    assert changed_right_lines(diff) == {"src/x.py": {2, 3}}


def _section(anchor_id: str = "a.b") -> SpecSection:
    return SpecSection(
        anchor_id=anchor_id,
        spec_path="openspec/specs/x/spec.md",
        heading_line=8,
        anchor_line=9,
        end_line=20,
    )


def _rule(anchor_id: str = "a.b", rule_id: str = "a.b__0") -> AnchorRule:
    return AnchorRule(
        anchor_id=anchor_id,
        rule_id=rule_id,
        rule={"kind": "function_definition"},
        files=["src/**"],
        sidecar="openspec/specs/x/anchors.yml",
    )


def test_classify_flags_a_dangling_rule() -> None:
    warnings = classify(
        rules=[_rule()],
        baseline={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        head={},
        changed={},
        sections={"a.b": _section()},
        touched_paths=set(),
    )
    assert [w.kind for w in warnings] == ["dangling"]
    assert warnings[0].anchor_id == "a.b"
    assert warnings[0].spec_path == "openspec/specs/x/spec.md"


def test_classify_never_flags_a_brand_new_anchor_as_dangling() -> None:
    # Rule matches nothing at the merge-base because the anchor (or the code)
    # didn't exist there yet.
    warnings = classify(
        rules=[_rule()],
        baseline={},
        head={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        changed={"src/x.py": {10}, "openspec/specs/x/spec.md": {9}},
        sections={"a.b": _section()},
        touched_paths={"src/x.py", "openspec/specs/x/spec.md"},
    )
    assert warnings == []


def test_classify_flags_drift_when_code_moved_and_spec_sat_still() -> None:
    warnings = classify(
        rules=[_rule()],
        baseline={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        head={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        changed={"src/x.py": {12}},
        sections={"a.b": _section()},
        touched_paths={"src/x.py"},
    )
    assert [w.kind for w in warnings] == ["drift"]
    assert "a.b" in warnings[0].detail


def test_classify_stays_quiet_when_the_pr_did_not_touch_anchored_code() -> None:
    warnings = classify(
        rules=[_rule()],
        baseline={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        head={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        changed={"src/other.py": {3}},
        sections={"a.b": _section()},
        touched_paths={"src/other.py"},
    )
    assert warnings == []


def test_classify_stays_quiet_when_the_spec_section_moved_too() -> None:
    warnings = classify(
        rules=[_rule()],
        baseline={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        head={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        changed={"src/x.py": {12}, "openspec/specs/x/spec.md": {10}},  # 10 is inside 8..20
        sections={"a.b": _section()},
        touched_paths={"src/x.py", "openspec/specs/x/spec.md"},
    )
    assert warnings == []


def test_classify_stays_quiet_when_the_sidecar_was_retuned() -> None:
    warnings = classify(
        rules=[_rule()],
        baseline={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        head={"a.b__0": [Match(file="src/x.py", start_line=5, end_line=30)]},
        changed={"src/x.py": {12}},
        sections={"a.b": _section()},
        touched_paths={"src/x.py", "openspec/specs/x/anchors.yml"},
    )
    assert warnings == []


def test_classify_reports_one_warning_per_anchor_not_per_rule() -> None:
    match = Match(file="src/x.py", start_line=5, end_line=30)
    warnings = classify(
        rules=[_rule(), _rule(rule_id="a.b__1")],
        baseline={"a.b__0": [match], "a.b__1": [match]},
        head={"a.b__0": [match], "a.b__1": [match]},
        changed={"src/x.py": {12}},
        sections={"a.b": _section()},
        touched_paths={"src/x.py"},
    )
    assert len(warnings) == 1
