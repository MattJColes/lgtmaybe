# review-pipeline Specification

## Purpose

The engine (`engine/engine.py`) that turns a PR context into findings: a
composable pipeline — redact → split → cap → static-analysis → triage →
expand → batch → fan-out per lens → parse → merge/dedupe → snap → reflect →
filter — with budget behaviors that degrade loudly, never silently.

## Requirements

### Requirement: The pipeline degrades loudly, never silently

`LLMReviewEngine.review` SHALL run the stages in order and, whenever any lens
call fails or is skipped, return partial results with a notice plus a hidden
incomplete marker — never a silent LGTM. A call skipped past either soft
whole-review ceiling — the deadline (`max_review_seconds`) or the billable-token
budget (`max_review_tokens`, off by default) — counts as a failed call, so the
ceilings are contributors to that notice rather than its only source. Any
stage failure surfaces to the caller.
<!-- anchor: engine.review -->

#### Scenario: a lens call fails
- **WHEN** a lens call raises (timeout, provider error) or returns unparseable
  output while others succeed
- **THEN** the summary carries the "N of M review calls failed" notice and the
  hidden incomplete marker the posting step keys on

#### Scenario: deadline passes mid-review
- **WHEN** lens calls are still queued after `max_review_seconds`
- **THEN** they are skipped and the summary carries the same partial-results notice

#### Scenario: token budget is exhausted mid-review
- **WHEN** the run's billable tokens reach `max_review_tokens` with calls queued
- **THEN** they are skipped and the summary names the budget alongside the
  partial-results notice, so a spend stop is never read as a clean review

#### Scenario: findings were suppressed
- **WHEN** a run suppresses findings (ignored fingerprint, inline pragma, or a
  previous run's 👎) and posts no others
- **THEN** the summary discloses the suppressed count instead of claiming LGTM

#### Scenario: earlier conversations are still open
- **WHEN** the PR carries unresolved conversations lgtmaybe opened on an earlier
  run and this run finds nothing
- **THEN** the summary says so instead of claiming LGTM — this run's count covers
  what it reviewed now, which an incremental run may not include

### Requirement: Per-lens fan-out through one bounded executor

Every `(batch, lens)` call SHALL run through one global bounded executor sized
by `max_concurrency` (auto: six, every provider). The
executor size SHALL determine only how the preset's calls are scheduled, never
how many there are. The rescue wave and the oversized-batch split each run in a
pool of their own, entered only after the fan-out's has closed or from a worker
that is already blocked — a worker that submits to its own pool and waits on the
result deadlocks once that pool saturates.
<!-- anchor: engine.fan-out -->

#### Scenario: parallel-capable default review
- **WHEN** a fast review uses cloud auto-concurrency
- **THEN** the security, correctness, code-health, and artefacts calls share the
  bounded executor and may overlap

#### Scenario: single-worker review
- **WHEN** a review sets `max_concurrency: 1` — a very slow local model, or a
  server whose own parallelism is one and whose calls must not queue
- **THEN** the same four calls run within the single-worker pool, serially

#### Scenario: deep audit
- **WHEN** a review uses the `full` preset
- **THEN** every built-in category runs, including tests and documentation

### Requirement: A transiently-failed call is re-run once

A `(batch, lens)` call that failed on the provider SHALL be re-run exactly once
after the fan-out drains, so one flaky call does not void the round's verdict.
Failures the reviewer's own request caused, and ceilings the user set, SHALL NOT
be re-run. Every rescue SHALL re-check the deadline, the token budget and the
interrupt first, and a run with no failures SHALL cost no extra calls.
<!-- anchor: engine.rescue -->

#### Scenario: one lens hits a transient provider failure
- **WHEN** a lens call fails on a rate limit, a 5xx or a stalled connection while
  its siblings succeed
- **THEN** it runs once more after the wave drains, and a review that recovers
  posts as complete instead of partial

#### Scenario: the failure would repeat identically
- **WHEN** a call returns unparseable output, hits the `max_tokens` ceiling,
  fails after the oversized-batch split already retried it smaller, or fails on
  a condition that cannot change mid-run — a spent quota, a dead credential
- **THEN** it is not re-run: the same request fails the same way, at cost

#### Scenario: the re-run fails too
- **WHEN** the rescue attempt fails as well
- **THEN** the round reports itself incomplete, naming the lens it lost, and no
  further attempt is made

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

### Requirement: Oversized single files are skipped and named

A file whose own patch exceeds `max_file_diff_lines` SHALL be dropped in the
same stage as the skip and path filters — before batching, so no model call and
no recursive walk ever sees it — and every dropped path SHALL be named in the
summary notice, because a silent drop reads as "everything was covered". The
skip SHALL NOT count towards `max_files`, exactly like a lockfile skip. `0`
disables the cap. This is the deterministic backstop for generated content the
name-based filter cannot recognise.
<!-- anchor: engine.size-cap -->

#### Scenario: a hand-named generated data blob
- **WHEN** the PR changes a 154,000-line `clause_index.json` and one source file
- **THEN** only the source file is reviewed and the summary names the skipped blob

#### Scenario: the cap is disabled
- **WHEN** `max_file_diff_lines` is `0`
- **THEN** no file is skipped for size and no size notice is posted

### Requirement: Over-budget files walk hunk-by-hunk

When one file's diff exceeds `max_input_tokens`, the engine SHALL decompose it
into per-hunk mini-diffs (each keeping its file header so line/side still bind
to the real diff) and batch those normally — nothing is dropped and each
call's context stays small. A hunk still over budget on its own SHALL be cut
further into budget-sized slices, each given a recomputed `@@` header. Files
within budget are reviewed whole.
<!-- anchor: engine.recursive-walk -->

#### Scenario: single file exceeds the budget
- **WHEN** a file's patch alone is over `max_input_tokens` and `recursive` is on
- **THEN** each of its hunks is reviewed as its own mini-diff

#### Scenario: a brand-new file is one enormous hunk
- **WHEN** the file has no hunk boundary to cut at and is over budget
- **THEN** the hunk itself is sliced to fit, line numbers still binding

### Requirement: An oversized batch is retried smaller, never repeated

A lens call that exhausts a per-request budget SHALL be retried on smaller
pieces of the same batch rather than re-sent unchanged, bounded to one split
level, the pieces reviewed concurrently, with the shrink disclosed in the
summary. Both budgets trigger it: the wall clock, and the `max_tokens` ceiling
an answer runs into. Findings the model completed before a truncation SHALL be
kept, and the lens SHALL still count as failed.
<!-- anchor: engine.timeout-split -->

#### Scenario: a multi-file batch times out
- **WHEN** a lens call on a batch of several files exceeds its wall-clock budget
- **THEN** the batch is halved by file and each half reviewed in its own call, and
  the summary reports that a batch was shrunk

#### Scenario: a single-file batch times out
- **WHEN** the timed-out batch holds one file
- **THEN** its hunks are divided into two groups, one review call each, so an
  oversized lone file still shrinks

#### Scenario: a call runs past its output ceiling
- **WHEN** a lens call's answer stops at the `max_tokens` ceiling
- **THEN** the batch is split the same way, and the findings finished before the
  cut are kept alongside the pieces' findings

#### Scenario: a piece exhausts its budget as well
- **WHEN** a piece of an already-split batch times out or truncates again
- **THEN** it fails as an ordinary failed call naming `max_tokens` — no recursion

#### Scenario: one piece answers and another fails
- **WHEN** part of a split batch is reviewed and part fails
- **THEN** the findings are kept AND the failure is reported, so the summary never
  claims a shrunk batch was reviewed when some of it was not

#### Scenario: the pieces are reviewed
- **WHEN** a batch is split
- **THEN** its pieces run together in an executor of their own, bounded by the
  backend's concurrency — never resubmitted into the pool this call occupies

### Requirement: A split is only attempted when covering less can help

A truncation that spent essentially the whole ceiling reasoning SHALL NOT be
split, and SHALL name `reasoning_effort` as the lever instead: a smaller payload
does not shrink a thinking budget, so the split would re-spend the whole
`max_tokens` ceiling on every piece and fail identically. The
decision reads the counts the failure carries, never its message. Findings
completed before the cut are kept on this path too, and a failure that says
nothing about size is not split at all.
<!-- anchor: engine.reasoning-ceiling -->

#### Scenario: the ceiling went on thinking
- **WHEN** a lens call truncates having spent nearly the whole ceiling reasoning
- **THEN** no pieces are reviewed, the salvage is kept, and the notice names
  `reasoning_effort` rather than `max_tokens`

#### Scenario: the ceiling went on the answer
- **WHEN** a truncated call spent only a small share of the ceiling reasoning
- **THEN** the batch is split as usual — that call really did have more to say
  than one response could hold

#### Scenario: the route reports no reasoning count
- **WHEN** a truncation carries no reasoning breakdown at all
- **THEN** the batch is split as usual, because silence is not evidence of thinking

#### Scenario: the failure says nothing about size
- **WHEN** a lens call fails for any other reason (quota, bad key, unparseable)
- **THEN** no split happens, because nothing suggests the payload was the problem

### Requirement: A lens may defer once for bounded read-only context

With `mid_review_retrieval` on, a lens that answers `needs` SHALL be re-run once
with those paths/symbols fetched read-only (redacted, capped at
`MAX_FETCH_FILES` files and a quarter of `max_input_tokens`) appended to its own
uncached block, never the shared prefix, and the two calls' findings merged.
Off (the default) `needs` is never parsed and every prompt is byte-identical.
<!-- anchor: engine.mid-review-retrieval -->

#### Scenario: a lens asks to read a file
- **WHEN** a lens answers `{"findings": [...], "needs": ["pkg/ledger.py"]}`
- **THEN** that file is fetched read-only and the lens is re-run once with it,
  and both calls' findings are kept and deduped

#### Scenario: the re-run asks again
- **WHEN** the re-run also answers with `needs`
- **THEN** it is ignored — one hop per (batch, lens), so at most one extra call

#### Scenario: nothing readable comes back
- **WHEN** every requested path/symbol resolves to nothing or exceeds the budget
- **THEN** the first call's findings stand, and the call is not a failure

#### Scenario: the deferral arrives past a ceiling
- **WHEN** the wall-clock deadline or token budget has passed when a lens defers
- **THEN** nothing is fetched, the first call's findings stand, and the run
  reports the existing incomplete-results notice

#### Scenario: retrieval is off or nothing can fetch
- **WHEN** `mid_review_retrieval` is off, or no read-only reader is injected
- **THEN** no lens is asked for `needs` and none is ever re-run

### Requirement: Context expansion is asymmetric and bounded

Hunks SHALL be padded with surrounding file content, budget-scaled and capped
by `context_lines`: the full budget before the hunk, a quarter (floored at one
line) after — the enclosing signature explains a change better than what
follows. Inline positions stay bound to the real diff, so context-only lines
never carry comments. Hunks whose padded windows meet are merged into one, with
the span between them filled in as context, so the patch stays monotonic and no
line is emitted twice. A definition widens the leading pad only while it still
CONTAINS the hunk — one that closed above it encloses nothing.
<!-- anchor: engine.context-expansion -->

#### Scenario: context lines never take comments
- **WHEN** a finding lands on an expansion-only line
- **THEN** it maps to nothing in the real diff and is dropped, never mis-posted

#### Scenario: two nearby hunks are padded into each other
- **WHEN** a hunk's leading pad reaches the previous hunk's trailing pad
- **THEN** both are emitted as one hunk whose header describes what it holds

#### Scenario: the change sits below a closed definition
- **WHEN** a hunk is on module-level code after a function has ended
- **THEN** the pad does not reach back into that function's body

### Requirement: Triage never skips past the security floor

With `triage_model` set, a cheap model SHALL rank files and skip
plainly-non-substantive ones — but a deterministic floor (security
paths/tokens, static-analysis hits, large hunks) always escalates past triage,
any triage failure reviews everything, and skips are named in the summary.
<!-- anchor: engine.triage -->

#### Scenario: triage tries to skip a security-sensitive file
- **WHEN** a file the cheap model would skip matches the security floor
- **THEN** the strong model reviews it anyway

### Requirement: Static analysis runs sandboxed

Installed tools SHALL run sandboxed when static analysis is enabled — scrubbed
env, no network, hard timeout, throwaway temp dir, never a checkout, and rules
read only from a local path — bundled with the package or configured — never a
network rule registry. Paths MUST be canonical forward-slash repository
paths. On Windows the scrubbed environment MUST pass through process-critical
system variables while pinning user config and profile directories to the temp
root. A tool that is not installed is skipped, and any tool failure degrades to
no output from that tool rather than failing the review.
<!-- anchor: engine.static-analysis -->

#### Scenario: a rules-driven tool has no rules
- **WHEN** static analysis runs a rules-driven tool for which no rules exist,
  neither bundled nor configured
- **THEN** that tool does not run at all (never a network rule registry)

#### Scenario: a Windows tool reports a backslash path
- **WHEN** static analysis reports `.\src\app.py`
- **THEN** the finding is associated with the canonical diff path `src/app.py`

#### Scenario: static analysis runs on Windows
- **WHEN** a child analyzer starts under Windows
- **THEN** it receives the minimal process-critical system variables and temp-
  rooted user directories without inheriting cloud credentials

### Requirement: Scan-only file texts never reach a prompt

Dependency manifests and lockfiles fetched for scanning SHALL travel in their
own context channel, separate from reviewed file texts. They MUST NOT enter the
diff, any prompt, a hint block, or the reflection pass, and they are fetched
only when a scanner will read them.
<!-- anchor: engine.scan-manifests -->

#### Scenario: a PR changes a lockfile
- **WHEN** a reviewed PR changes a lockfile and static analysis is enabled
- **THEN** the scanner reads it and no model call contains any of its content

#### Scenario: static analysis is off
- **WHEN** static analysis is disabled
- **THEN** no dependency manifest is fetched at all

### Requirement: Tool findings ground or post, by mode

Each tool's findings SHALL reach the review by its configured mode: `hint`
wraps them as untrusted grounding for the lens calls, while `finding` maps them
onto review findings posted with no model call. Direct-posted findings MUST be
redacted, MUST carry a `scan:<tool>` category, and MUST be re-anchored, deduped,
suppressed and rule-filtered like any other finding — but never reflected, since
there is no model judgement to audit. A direct finding whose anchor matches no
changed line MUST be dropped and counted in the summary, because the tools read
whole files and only the diff is under review.
<!-- anchor: engine.tool-findings -->

#### Scenario: a scanner hits a line the PR changed
- **WHEN** a `finding`-mode tool reports a credential on a changed line
- **THEN** the review posts it without any model call, and no raw secret value
  appears in the finding

#### Scenario: a scanner hits pre-existing code
- **WHEN** a `finding`-mode tool reports an issue on an unchanged line
- **THEN** the finding is dropped and the summary states how many were skipped

#### Scenario: a dependency advisory has no line to anchor to
- **WHEN** a vulnerability scanner reports an advisory against a lockfile the
  review never anchors against
- **THEN** the finding is exempt from that drop and renders in the review body
