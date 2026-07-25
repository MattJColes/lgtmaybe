# review-pipeline Specification

## Purpose

The engine (`engine/engine.py`) that turns a PR context into findings: a
composable pipeline — redact → split → cap → static-analysis → triage →
expand → batch → fan-out per lens → parse → merge/dedupe → snap → reflect →
filter — with budget behaviors that degrade loudly, never silently.

## Requirements

### Requirement: The pipeline degrades loudly, never silently

`LLMReviewEngine.review` SHALL run the stages in order and, when the soft
whole-review deadline (`max_review_seconds`) passes, skip still-queued calls
and return partial results with a notice — never a silent LGTM. Any stage
failure surfaces to the caller.
<!-- anchor: engine.review -->

#### Scenario: deadline passes mid-review
- **WHEN** lens calls are still queued after `max_review_seconds`
- **THEN** they are skipped and the summary carries a partial-results notice

### Requirement: Per-lens fan-out through one bounded executor

Every (batch, lens) call SHALL run through one global thread pool sized by
`max_concurrency` (auto: 8 cloud, 1 for ollama/openai-compatible), so local
servers are never flooded and cloud reviews parallelise.
<!-- anchor: engine.fan-out -->

#### Scenario: local provider stays serial
- **WHEN** the provider is ollama and `max_concurrency` is unset
- **THEN** lens calls run one at a time

### Requirement: Findings merge and dedupe across lenses

After the fan-out, findings SHALL be merged and de-duplicated keyed on
path/line/side, so overlapping lenses never post the same comment twice.
<!-- anchor: engine.dedupe -->

#### Scenario: two lenses flag the same line
- **WHEN** security and correctness both return a finding on one line
- **THEN** a single finding survives the merge

### Requirement: Line anchoring never trusts model arithmetic

Each finding carries a verbatim `anchor` line; the engine SHALL re-anchor
`line` to the real changed line whose content matches (exact → whitespace →
unique substring, nearest-to-model-line tiebreak). An anchor matching nothing
marks the finding `anchored=False` so the gateway demotes it rather than
posting on a wrong line.
<!-- anchor: engine.snap -->

#### Scenario: model miscounts the line
- **WHEN** a finding's `line` is off but its `anchor` text matches a changed line
- **THEN** the finding snaps to the matching line before posting

### Requirement: Path filters apply after the skip filter

The user's `include_paths` allowlist and `exclude_paths` denylist SHALL apply
right after generated/binary skipping; exclude wins, `**/`-prefixed patterns
also match at the repo root, and matching repository paths is case-sensitive
on every host.
<!-- anchor: engine.path-filters -->

#### Scenario: a file is both included and excluded
- **WHEN** a path matches `include_paths` and `exclude_paths`
- **THEN** it is excluded

#### Scenario: path case differs from the configured glob
- **WHEN** a repository path differs from a configured include or exclude glob
  only by letter case
- **THEN** the path does not match on Windows or POSIX hosts

### Requirement: Over-budget files walk hunk-by-hunk

When one file's diff exceeds `max_input_tokens`, the engine SHALL decompose it
into per-hunk mini-diffs (each keeping its file header so line/side still bind
to the real diff) and batch those normally — nothing is dropped and each
call's context stays small. Files within budget are reviewed whole.
<!-- anchor: engine.recursive-walk -->

#### Scenario: single file exceeds the budget
- **WHEN** a file's patch alone is over `max_input_tokens` and `recursive` is on
- **THEN** each of its hunks is reviewed as its own mini-diff

### Requirement: Context expansion is asymmetric and bounded

Hunks SHALL be padded with surrounding file content, budget-scaled and capped
by `context_lines`: the full budget before the hunk, a quarter (floored at one
line) after — the enclosing signature explains a change better than what
follows. Inline positions stay bound to the real diff, so context-only lines
never carry comments.
<!-- anchor: engine.context-expansion -->

#### Scenario: context lines never take comments
- **WHEN** a finding lands on an expansion-only line
- **THEN** it maps to nothing in the real diff and is dropped, never mis-posted

### Requirement: Triage never skips past the security floor

With `triage_model` set, a cheap model SHALL rank files and skip
plainly-non-substantive ones — but a deterministic floor (security
paths/tokens, static-analysis hits, large hunks) always escalates past triage,
any triage failure reviews everything, and skips are named in the summary.
<!-- anchor: engine.triage -->

#### Scenario: triage tries to skip a security-sensitive file
- **WHEN** a file the cheap model would skip matches the security floor
- **THEN** the strong model reviews it anyway

### Requirement: Static analysis grounds, never posts

Installed tools SHALL run sandboxed when static analysis is enabled — ruff,
bandit, and semgrep with local rules only; scrubbed env, no network, hard
timeout, temp dir, never a checkout — and their findings enter the prompt only
as an untrusted HINTS block ("confirm, contextualise, or discard"). Paths MUST
be canonical forward-slash repository paths. On Windows the scrubbed
environment MUST pass through process-critical system variables while pinning
user config and profile directories to the temp root. Raw tool findings are
never posted; a missing tool is skipped silently.
<!-- anchor: engine.static-analysis -->

#### Scenario: semgrep has no local rules
- **WHEN** static analysis runs without `semgrep_rules` configured
- **THEN** semgrep does not run at all (never `--config auto`)

#### Scenario: a Windows tool reports a backslash path
- **WHEN** static analysis reports `.\src\app.py`
- **THEN** the hint is associated with the canonical diff path `src/app.py`

#### Scenario: static analysis runs on Windows
- **WHEN** a child analyzer starts under Windows
- **THEN** it receives the minimal process-critical system variables and temp-
  rooted user directories without inheriting cloud credentials
