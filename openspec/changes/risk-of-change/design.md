# Design

## Context

See proposal.md for motivation. What this builds on:

- `engine/high_impact.py` already has deterministic per-area path regexes
  (`path_signals`) and a model call. Only the regexes are reused here.
- `engine/labels.py` already computes `review-effort/1-5` from
  `core.diffparse.changed_line_count`, and the GitHub adapter reconciles
  lgtmaybe's own label families.
- `LLMReviewEngine` holds `workspace_root` (default `Path.cwd()`): the trusted
  base checkout on `pull_request_target`, the repo itself for the local CLI. The
  spec lens and directory rules already read it.
- Under incremental review the engine replaces `ctx.diff` with the compare diff,
  but `ctx.changed_files` stays the full PR list.
- `ast-grep-cli` is a runtime dependency, present in the GHCR image and the
  Windows executable.
- The overview (`engine/overview.py`) and the review summary are built in
  separate code paths, and the overview can be stood down.
- `--json` output is a bare array of findings.

## Goals / Non-Goals

**Goals:**
- One verdict that reads the same in the summary, the overview, the marker and
  the label for a given head.
- Zero configuration gives a useful result on Python and TS/JS repos.
- No model call, no new dependency, no new comment.

**Non-Goals:**
- Auto-approve. CLAUDE.md is explicit that enforcement rides a Check Run and
  approval state is never set. The marker and label let teams build a gate.
- A `fail_on`-style Check Run keyed on risk. The marker makes this possible
  later. Not in this change.
- A findings-confidence score (Greptile's 0-5). The verdict describes the
  change, not the review.
- Churn, ownership (CODEOWNERS) or history-based signals.
- Languages beyond Python, TypeScript and JavaScript for blast radius.
- A structured `risk` field in `--json`. Wrapping the array in an envelope is a
  breaking change, and it deserves its own proposal.

## Decisions

**D1. The verdict is deterministic, and the model never moves it.** Earlier we
discussed letting the High Impact model call raise the level. That was dropped:
the overview and the summary are separate code paths, and the overview can be
skipped, so a model-influenced level would disagree between surfaces and shift
between re-runs. A deterministic verdict is also free, which is what makes it
acceptable on every review by default, and a gate can rely on it. High Impact
Areas keeps its model reasoning in its own section.

**D2. Blast radius comes from an ast-grep import scan, resolved to paths.**
One `ast-grep scan --inline-rules --json` over the workspace extracts import
statements. For Python, that's `import_statement` and `import_from_statement`.
For TS/JS, it's `import_statement`, `export ... from`, and `require(...)` /
`import(...)` calls with a string literal. Resolution:
- Python: a dotted name `a.b` resolves to `a/b.py` or `a/b/__init__.py`, matched
  against changed paths by path suffix so `src/` layouts work. Relative imports
  resolve from the importer's package.
- TS/JS: only relative specifiers (`./`, `../`) resolve, probing
  `.ts .tsx .js .jsx .mjs .cjs` and `/index.*`. Bare specifiers (packages,
  tsconfig path aliases) are ignored in v1.

Alternatives rejected:
- `git grep` for the module stem. Matches comments and strings, and common
  stems like `utils` collide across packages.
- A language server or full import graph. Heavy, per-language setup, and
  CLAUDE.md warns against speculative frameworks.

ast-grep respects `.gitignore` by default, so `node_modules` and virtualenvs are
skipped.

Importers that the PR itself adds or changes are excluded, as are test files
(a test importing a module is its coverage, not a dependent), and a file the PR
adds scores zero. This makes the count identical whether the workspace is the
base checkout (Action) or the PR head (local CLI, GitLab CI), and it keeps the
meaning to one thing: the existing code this change can break. The PR's own
importers are already in the diff. Added files are read from the diff's
`new file mode` / `--- /dev/null` headers.

A timeout or a non-zero ast-grep exit discards partial output and marks every
supported changed file unassessed. Partial counts would look precise without
being so. Anything else raised inside `assess_risk` becomes the unavailable
verdict.

**D3. The scan is bounded and never reaches the model.** It runs through the
same subprocess helpers as `engine/static_analysis.py`: scrubbed environment and
a hard timeout (20s). It parses files and executes nothing. It runs at most once
per run: `run_review` assesses the full PR once and carries the result on
`PRContext.risk`, which the summary, the labels and the overview all read (the
engine assesses on demand only when a caller didn't, e.g. the local CLI). Importer paths only feed counts and the rendered reasons, and
are never put in a prompt. Rendered paths strip backticks, the same as
`high_impact._paths`, because filenames are attacker-chosen on a fork PR.

**D4. One module, one entry point.** `engine/risk.py` exposes
`assess_risk(ctx, cfg, workspace_root) -> RiskAssessment` plus two renderers:
`risk_segment` for the summary line and `render_risk_section` for the overview.
`RiskAssessment` (in `core/models.py`) holds the level, reasons (each with its
factor, level and text) and the unassessed paths. It lives in `engine/`, not an
adapter, so GitHub, GitLab, Gitea and the local CLI all get it (see "Forges" in
CLAUDE.md).

**D5. Assess before scoping.** `run_review` calls `assess_risk` on the context as
fetched, before incremental, triage or `max_files` narrowing, so size is
measured over the full PR diff (spec: "The verdict covers the whole pull
request"). The overview receives the unscoped context already.

**D6. Level rules are fixed constants.** The thresholds (5/20 importers, 500
changed lines, 50 untested lines) are the spec's contract, and live as module
constants rather than config. They get tuned once we've seen real repos, and a
knob can come later if someone asks.

Test and doc detection uses a path heuristic:
- Test: segments `tests/`, `test/`, `__tests__/`, `spec/`, or files
  `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`, `*.spec.*`.
- Doc: `*.md`, `*.rst`, `*.adoc`, or anything under `docs/`.

**D7. Reason ranking.** Reasons sort by the level they raise, then by factor
order: blast radius, High Impact area, size, missing tests. The summary shows
the first, followed by `(+N more)` when there are others. An unassessed caveat
is always rendered alongside a `low` level (spec: "Unmeasured blast radius is
named, never scored low").

**D8. Config shape follows `StaticAnalysisConfig`.** Nested
`RiskConfig(enabled: bool = True, core_paths: list[str] = [])` on
`ReviewConfig.risk`:
- CLI: `--risk/--no-risk` maps to `enabled`.
- Action: input `risk` (empty means the default), handled like `static_analysis`.
- YAML: `core_paths` is set here only, like other list-valued config.

`core_paths` globs reuse `engine.passes_path_filters` semantics, so `**/`
patterns match at the repo root too.

**D9. Summary line.** The default line becomes
`3 findings · risk high (<reason>) · provider … · model … · lgtmaybe …`.
`summary_template` gains `{risk}`, which renders the same segment text. The
hidden `<!-- lgtmaybe-risk:<level> -->` marker is appended next to the lenses
marker on every summary shape (notices, LGTM, findings). An unavailable
assessment renders `risk unavailable` and emits no marker, because a gate should
treat a missing marker as unknown rather than read a guessed level.

## Risks / Trade-offs

- [TS barrel files and path aliases undercount importers: importers of
  `index.ts` don't count toward the module it re-exports] → `core_paths`
  covers the known hubs. Resolving tsconfig `paths` and re-exports is a
  follow-up.
- [A large monorepo scan exceeds 20s] → the timeout marks code files unassessed,
  and the rest of the verdict still stands. Carrying the result on the context
  means we pay it once per run.
- [On GitLab CI the workspace is the MR head, not a trusted base] → the scan is
  read-only parsing, and nothing from it reaches the model. An author can
  inflate their own risk (harmless). Deflating it means deleting importers,
  which shows in the diff and adds changed lines.
- [A default-on verdict on every PR becomes noise] → it's one segment on a line
  that already exists. `low` states its reason ("docs and tests only") so it
  reads as information, not filler.
- [Heuristic test detection misses a repo's convention] → the missing-tests
  factor only raises to `medium`, never higher, so a miss costs one level at
  most.

## Migration Plan

Ships as a `feat:` commit, so release-please cuts a minor version. Existing
installs see the risk segment and marker on their next review with no config
change. Users with a custom `summary_template` see no visible change until they
add `{risk}`. To roll back per repo, set `risk: {enabled: false}` or the Action
input `risk: false`. Labels only appear for repos that already opted
into `pr_labels`.

## Open Questions

- Threshold tuning. Run the scan over a few real repos (this one included) after
  it lands and adjust the constants if `high` fires on most PRs. Changing a
  constant doesn't change the approach or the task breakdown.
