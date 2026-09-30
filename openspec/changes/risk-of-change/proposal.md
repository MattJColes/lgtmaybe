# Proposal

## Why

A reviewer opening a PR has no quick answer to "how far could this reach?".
High Impact Areas names sensitive paths, but it can't see that a small edit to a
shared core module is imported by most of the codebase, and it lives only in the
overview comment, which is skipped when the PR already has a diagram. Greptile
and similar tools lead with a single risk tier for exactly this reason. We want
the same thing on every review by default, computed without a model call so it
is stable, free, and something a merge gate can rely on.

## What Changes

- New deterministic **risk-of-change** assessment: `low | medium | high |
  critical` plus the reasons behind it, from four factors: blast radius (how many
  files in the workspace import each changed file, via ast-grep, plus optional
  `core_paths` globs), the existing High Impact path signals, change size, and
  whether code changed without any test changing. No model call.
- **On by default.** Every review summary carries the verdict and its top reason
  in the summary line. The overview gains a `Risk of Change` section above High
  Impact Areas listing every reason.
- Every summary carries a hidden `<!-- lgtmaybe-risk:<level> -->` marker so a
  posting gate or workflow can read the level.
- With `pr_labels` on, a `risk/<level>` label joins the existing label families.
- When blast radius can't be measured (no workspace, scan timeout, unsupported
  language) the verdict says so for the affected files and never reports those
  files as low risk.
- The verdict describes the whole PR, so an incremental re-review reports the
  same level as a full one.
- Config: `risk.enabled` (default on), `risk.core_paths` (default empty); CLI
  `--risk/--no-risk`; Action input `risk_of_change`. `summary_template` gains a
  `{risk}` placeholder.
- Local `lgtmaybe review` shows the verdict via the summary it already prints
  (`human` and `agent` formats). `--json` keeps its current array shape.

## Capabilities

### New Capabilities
- `risk-of-change`: the deterministic risk assessment, its factors and levels,
  how it degrades when blast radius can't be measured, and where it renders
  (summary line, hidden marker, overview section).

### Modified Capabilities
- `github-posting`: the "Labels touch only our own families" requirement gains
  the `risk/<level>` family.

## Impact

- New `src/lgtmaybe/engine/risk.py`; touches `engine/engine.py` (summary line,
  marker, assessing before incremental scoping), `engine/overview.py` (new
  section), `engine/labels.py`, `core/models.py` (`RiskConfig`,
  `ReviewConfig.risk`), `cli/` (flag, Action input wiring), `action.yml`,
  `config/` loader.
- Reuses `high_impact.path_signals` and the existing `ast-grep-cli` runtime
  dependency (already bundled in the image and the Windows executable). No new
  dependency.
- Reads the workspace (the trusted base checkout on `pull_request_target`, the
  repo itself locally) read-only. Nothing from the scan reaches the model.
- Every review summary changes: one extra segment on the summary line and one
  hidden marker. Users with `summary_template` set see no visible change unless
  they add `{risk}`.
- Docs: generated config reference, the Action inputs reference, and a
  how-to/explanation page for reading the verdict.
