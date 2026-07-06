"""Spec-drift gate: warn when anchored code changes and its spec section doesn't.

Every requirement section in openspec/specs/<capability>/spec.md carries an
anchor id (`<!-- anchor: ... -->`) bound to code by ast-grep rules in the
capability's anchors.yml sidecar. On a PR this script:

1. scans the sidecar rules at HEAD and at the merge-base with the target branch
   (a temporary git worktree — the scan cwd matters: `files:` globs resolve
   against it, so both scans run from a repo root targeting src/);
2. flags DANGLING rules — matched at the base, match nothing now (a rename or
   removal nobody re-pointed; intersection alone goes blind here);
3. flags DRIFT — a rule's match intersects the PR's changed lines while the diff
   touched neither that anchor's spec section nor its sidecar.

Warnings only — the exit code is always 0. A stale spec is not a broken build,
and a blocking doc check just gets gamed. The deterministic half of the
convention (each rule resolves to exactly one place, ids are a bijection) is a
hard gate in tests/specs/test_anchors.py instead.

Usage: uv run python scripts/check_spec_drift.py --base origin/main
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from lgtmaybe.core.diffparse import changed_line_index

ANCHOR_RE = re.compile(r"^<!-- anchor: (?P<id>[a-z0-9.-]+) -->$")
SCAN_TARGET = "src/"


@dataclass(frozen=True)
class AnchorRule:
    """One ast-grep rule from a sidecar, addressable as anchor_id + __N suffix."""

    anchor_id: str
    rule_id: str
    rule: dict[str, object]
    files: list[str]
    sidecar: str  # repo-relative posix path of the anchors.yml it came from


@dataclass(frozen=True)
class Match:
    """One ast-grep match, 1-based inclusive line range."""

    file: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SpecSection:
    """A requirement section's location inside its spec.md (1-based lines)."""

    anchor_id: str
    spec_path: str
    heading_line: int
    anchor_line: int
    end_line: int


@dataclass(frozen=True)
class DriftWarning:
    kind: str  # "dangling" | "drift"
    anchor_id: str
    spec_path: str
    anchor_line: int
    detail: str


def load_anchors(root: Path) -> list[AnchorRule]:
    """Parse every openspec/specs/*/anchors.yml under *root*.

    A sidecar maps anchor id → either one ``{rule, files}`` object or a list of
    them; both normalise to one AnchorRule per rule with an ``__N`` suffix.
    """
    rules: list[AnchorRule] = []
    for sidecar in sorted(root.glob("openspec/specs/*/anchors.yml")):
        loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
        rel = sidecar.relative_to(root).as_posix()
        for anchor_id, entry in loaded.items():
            entries = entry if isinstance(entry, list) else [entry]
            for n, item in enumerate(entries):
                rules.append(
                    AnchorRule(
                        anchor_id=anchor_id,
                        rule_id=f"{anchor_id}__{n}",
                        rule=item["rule"],
                        files=list(item["files"]),
                        sidecar=rel,
                    )
                )
    return rules


def to_inline_rules(rules: list[AnchorRule]) -> str:
    """Translate sidecar rules into one ast-grep scan --inline-rules string."""
    docs = []
    for r in rules:
        docs.append(
            yaml.safe_dump(
                {"id": r.rule_id, "language": "python", "files": r.files, "rule": r.rule},
                sort_keys=False,
            )
        )
    return "\n---\n".join(docs)


def parse_scan_output(json_text: str) -> dict[str, list[Match]]:
    """Group ast-grep --json output by rule id, converting to 1-based lines."""
    matches: dict[str, list[Match]] = {}
    for item in json.loads(json_text or "[]"):
        matches.setdefault(item["ruleId"], []).append(
            Match(
                file=item["file"],
                start_line=item["range"]["start"]["line"] + 1,
                end_line=item["range"]["end"]["line"] + 1,
            )
        )
    return matches


def run_scan(binary: str, inline_rules: str, cwd: Path) -> dict[str, list[Match]]:
    """Run one ast-grep scan from *cwd* (so files: globs resolve) over src/."""
    result = subprocess.run(
        [binary, "scan", "--inline-rules", inline_rules, "--json", SCAN_TARGET],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return parse_scan_output(result.stdout)


def extract_sections(spec_md: str, spec_path: str) -> dict[str, SpecSection]:
    """Map anchor id → its requirement section's line range in *spec_md*.

    The anchor comment sits inside its ``### Requirement:`` section — by
    convention on its own line right after the opening paragraph (OpenSpec's
    parser reads the first block after the heading as the requirement text, so
    the comment can't sit above it). A section runs from its heading to the
    line before the next ``##``/``###`` heading (or EOF). A comment outside any
    requirement section is skipped — the hygiene test flags that shape error.
    """
    lines = spec_md.splitlines()
    sections: dict[str, SpecSection] = {}
    heading: int | None = None  # 1-based line of the enclosing requirement heading
    for i, line in enumerate(lines):
        if line.startswith("### Requirement:"):
            heading = i + 1
            continue
        if line.startswith("### ") or line.startswith("## "):
            heading = None
            continue
        m = ANCHOR_RE.match(line.strip())
        if not m or heading is None:
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if lines[j].startswith("### ") or lines[j].startswith("## "):
                end = j
                break
        anchor_id = m.group("id")
        sections[anchor_id] = SpecSection(
            anchor_id=anchor_id,
            spec_path=spec_path,
            heading_line=heading,
            anchor_line=i + 1,
            end_line=end,
        )
    return sections


def changed_right_lines(diff: str) -> dict[str, set[int]]:
    """RIGHT-side (new-file) changed line numbers per path, from a unified diff."""
    changed: dict[str, set[int]] = {}
    for (path, side), entries in changed_line_index(diff).items():
        if side == "RIGHT":
            changed.setdefault(path, set()).update(line for line, _ in entries)
    return changed


def classify(
    *,
    rules: list[AnchorRule],
    baseline: dict[str, list[Match]],
    head: dict[str, list[Match]],
    changed: dict[str, set[int]],
    sections: dict[str, SpecSection],
    touched_paths: set[str],
) -> list[DriftWarning]:
    """The two checks, per rule, deduped to one warning per (kind, anchor)."""
    warnings: dict[tuple[str, str], DriftWarning] = {}
    for rule in rules:
        section = sections.get(rule.anchor_id)
        spec_path = section.spec_path if section else rule.sidecar
        anchor_line = section.anchor_line if section else 1
        head_matches = head.get(rule.rule_id, [])
        if not head_matches:
            if baseline.get(rule.rule_id):
                warnings.setdefault(
                    ("dangling", rule.anchor_id),
                    DriftWarning(
                        kind="dangling",
                        anchor_id=rule.anchor_id,
                        spec_path=spec_path,
                        anchor_line=anchor_line,
                        detail=(
                            f"anchor {rule.anchor_id} is dangling — {rule.rule_id} matched at "
                            f"the merge-base but matches nothing now (renamed or removed?); "
                            f"re-point the rule in {rule.sidecar} and re-read its spec section"
                        ),
                    ),
                )
            continue
        hit = any(
            changed.get(m.file, set()) & set(range(m.start_line, m.end_line + 1))
            for m in head_matches
        )
        if not hit:
            continue
        section_touched = rule.sidecar in touched_paths or (
            section is not None
            and changed.get(section.spec_path, set())
            & set(range(section.heading_line, section.end_line + 1))
        )
        if not section_touched:
            warnings.setdefault(
                ("drift", rule.anchor_id),
                DriftWarning(
                    kind="drift",
                    anchor_id=rule.anchor_id,
                    spec_path=spec_path,
                    anchor_line=anchor_line,
                    detail=(
                        f"code under anchor {rule.anchor_id} changed but its spec section in "
                        f"{spec_path} did not — update the section or confirm it still holds"
                    ),
                ),
            )
    return list(warnings.values())


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def _load_sections(root: Path) -> dict[str, SpecSection]:
    sections: dict[str, SpecSection] = {}
    for spec in sorted(root.glob("openspec/specs/*/spec.md")):
        rel = spec.relative_to(root).as_posix()
        sections.update(extract_sections(spec.read_text(encoding="utf-8"), rel))
    return sections


def _report(warnings: list[DriftWarning]) -> None:
    for w in warnings:
        print(f"::warning file={w.spec_path},line={w.anchor_line}::{w.detail}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = ["## Spec drift", ""]
    if warnings:
        lines += ["| anchor | kind | detail |", "|---|---|---|"]
        lines += [f"| `{w.anchor_id}` | {w.kind.upper()} | {w.detail} |" for w in warnings]
    else:
        lines.append("No drift: anchored code and spec sections moved together (or not at all).")
    body = "\n".join(lines) + "\n"
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(body)
    else:
        print(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="ref to diff against (merge-base)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    binary = shutil.which("ast-grep")
    if binary is None:
        print("::warning::ast-grep binary not found — spec-drift check skipped")
        return 0
    rules = load_anchors(root)
    if not rules:
        print("no anchor rules found — nothing to check")
        return 0

    merge_base = _git(root, "merge-base", args.base, "HEAD")
    diff = _git(root, "diff", f"{merge_base}...HEAD")
    inline = to_inline_rules(rules)
    head_matches = run_scan(binary, inline, root)

    worktree = Path(tempfile.mkdtemp(prefix="spec-drift-base-"))
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), merge_base)
        baseline = run_scan(binary, inline, worktree)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=root,
            capture_output=True,
        )

    warnings = classify(
        rules=rules,
        baseline=baseline,
        head=head_matches,
        changed=changed_right_lines(diff),
        sections=_load_sections(root),
        touched_paths={path for path, _ in changed_line_index(diff)},
    )
    _report(warnings)
    return 0  # warnings only, by design — see module docstring


if __name__ == "__main__":
    sys.exit(main())
