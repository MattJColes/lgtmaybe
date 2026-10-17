# review-pipeline Specification

## Purpose

The engine (`engine/engine.py`, its preparation stages in
`engine/stages.py`) that turns a PR context into findings: a
composable pipeline — redact → split → cap → static-analysis → triage →
expand → batch → fan-out per lens → parse → merge/dedupe → snap → reflect →
filter — with budget behaviors that degrade loudly, never silently. The
budget and truncation ladder live in `budget-and-truncation`; static analysis
and triage in `static-analysis-and-triage`.

## Requirements

### Requirement: The pipeline degrades loudly, never silently

`LLMReviewEngine.review` SHALL run the stages in order and, whenever any lens
call fails or is skipped, return partial results with a notice plus a hidden
incomplete marker — never a silent LGTM. A call skipped past either soft
whole-review ceiling — the deadline (`max_review_seconds`) or the billable-token
budget (`max_review_tokens`, off by default) — counts as a failed call, so the
ceilings are contributors to that notice rather than its only source. Any
stage failure surfaces to the caller, as does reviewable code that no
configured lens could run over (e.g. `categories: [spec]` with no matching spec).
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
- **WHEN** the review's own billable tokens (never another review's on the same
  engine or process) reach `max_review_tokens` with calls queued
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

### Requirement: The summary names the lenses that completed

Every summary SHALL carry a hidden `<!-- lgtmaybe-lenses:… -->` marker listing,
sorted and comma-separated, the lenses that completed every call they were
given. A gate that wants "every lens ran" needs a positive list: a lens that
never ran leaves no failure notice to find. A lens with any failed or skipped
call is left out — the failure notice already names it — and the marker family
is disjoint from the summary, finding, reviewed and incomplete markers.
<!-- anchor: engine.lenses-marker -->

#### Scenario: a clean fast-preset round
- **WHEN** all four fast-preset calls succeed and no spec matched
- **THEN** the summary ends with `lgtmaybe-lenses:artefacts,code-health,correctness,security`

#### Scenario: one lens fails
- **WHEN** the security lens's call raises and the others succeed
- **THEN** the marker lists the other three and the incomplete notice names security

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
- **THEN** the security, correctness, code-health, artefacts, and architecture
  calls share the bounded executor and may overlap

#### Scenario: single-worker review
- **WHEN** a review sets `max_concurrency: 1` — a very slow local model, or a
  server whose own parallelism is one and whose calls must not queue
- **THEN** the same five calls run within the single-worker pool, serially

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
- **THEN** the identical request is not re-issued: it fails the same way, at
  cost. A reply that could not be parsed may still be reformatted once by a
  DIFFERENT request (see "An unparseable reply is reformatted once")

#### Scenario: a split piece fails on the provider
- **WHEN** an oversized batch was split and a piece failed on the provider
- **THEN** the rescue re-runs only the pieces that failed that way, never the
  oversized whole batch nor the pieces that answered

#### Scenario: the re-run fails too
- **WHEN** the rescue attempt fails as well
- **THEN** the round reports itself incomplete, naming the lens it lost, and no
  further attempt is made

### Requirement: An unparseable reply is reformatted once

A review reply that could not be parsed into findings SHALL be sent back to the
model exactly once — carrying the reply and the output schema, and no diff —
asking for it in the required shape. This is a different request from the one
that failed, which is why it is not the identical re-run the rescue wave
forbids. It SHALL be skipped for a truncated or empty reply, SHALL re-check
every ceiling first, SHALL never reformat its own output, and SHALL be able only
to ADD findings: any failure leaves the call's existing failure reason intact.
A reformatted lens SHALL count as complete and SHALL be named in the summary,
since a model that needs this is not honouring the schema.
<!-- anchor: engine.repair -->

#### Scenario: the model answers in prose
- **WHEN** a lens returns a complete review in the wrong wrapper
- **THEN** it is reformatted into findings, and the lens posts as complete
  rather than reporting nothing

#### Scenario: the reply was cut off
- **WHEN** a reply hit the output ceiling mid-container
- **THEN** it is not reformatted — its complete findings are already salvaged,
  and asking a model to finish a cut-off answer invites it to invent the tail

#### Scenario: the reformat fails too
- **WHEN** the reformatted reply is itself unparseable, or the call raises
- **THEN** no findings are added and the original failure reason stands, unless
  the schema-less re-run below recovers the lens

### Requirement: A schema-bound parse failure is re-asked without the schema

A parse failure on a call that sent the provider's JSON schema SHALL be re-asked
once with the schema omitted, when the reformat above could not recover it.
This is the last of three structured-output fallbacks and the only one the
adapter cannot detect for itself: a reply that is non-empty and well-formed on
the wire, but is not findings, looks like a clean success from there. It SHALL
run only after the reformat, which is an order of magnitude cheaper; SHALL never
re-ask its own output; and SHALL re-check every ceiling first. A re-run that
parses SHALL mark that model so later calls skip the schema, and SHALL be named
in the summary; a re-run that fails SHALL mark nothing, since one bad reply does
not prove a broken schema mode.
<!-- anchor: engine.schemaless-retry -->

#### Scenario: the model's JSON mode produces unreadable replies
- **WHEN** a lens returns non-empty prose under `response_format` and the
  reformat cannot recover it
- **THEN** the lens is re-run without the schema and its findings post as
  complete

#### Scenario: no schema was ever sent
- **WHEN** the same failure happens with structured output off
- **THEN** nothing is re-asked, because the re-run would be the identical
  request the rescue wave forbids

#### Scenario: the re-run fails too
- **WHEN** the schema-less reply is unparseable as well
- **THEN** the original reason stands, no further call is made, and the model is
  not marked

### Requirement: Findings merge and dedupe across lenses

After the fan-out, findings SHALL be merged and de-duplicated keyed on
path/line/side, so overlapping lenses never post the same comment twice. The
merge SHALL run after suppression and the evidence gate, so a copy those drop
never displaces its location's surviving finding. At a shared key the merge
SHALL prefer an anchored finding over an unanchored one, because an unanchored
line is the model's guess. Scan findings SHALL also be
keyed on their title, so distinct tool hits never merge with each other or with
a model finding.
<!-- anchor: engine.dedupe -->

#### Scenario: two lenses flag the same line
- **WHEN** security and correctness both return a finding on one line
- **THEN** a single finding survives the merge

#### Scenario: the winning copy is gated
- **WHEN** a critical finding with no failure scenario and an evidenced medium
  finding share a line
- **THEN** the evidenced finding survives, and the review is not reported clean

#### Scenario: an unanchored copy would win the location
- **WHEN** an anchored medium finding and an unanchored medium finding with a
  longer body share a line
- **THEN** the anchored finding survives, so the unanchored severity floor
  cannot empty the review into a false clean verdict

#### Scenario: two advisories at one lockfile line
- **WHEN** osv-scanner reports two advisories, both at line 1 of `uv.lock`
- **THEN** both findings survive the merge

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

### Requirement: Profile traces finding flow

The opt-in review profile SHALL report the parsed finding count for every
successful review-lens call and SHALL compare their total with the count
returned after the pipeline. A valid empty findings payload MUST render as
zero, a parse failure MUST render as an error rather than zero, and calls that
do not produce review findings MUST remain uncounted.
<!-- anchor: engine.profile-findings -->

#### Scenario: model returns valid empty findings
- **WHEN** every review lens returns a valid empty findings payload
- **THEN** each review call reports zero parsed findings and the profile reports zero parsed and zero returned

#### Scenario: response cannot be parsed
- **WHEN** a provider call succeeds but its response is not valid findings JSON
- **THEN** that call's profile row reports the parse failure and does not report zero parsed findings

#### Scenario: downstream stages remove findings
- **WHEN** review calls parse one or more findings that dedupe, suppression,
  reflection, or filtering later removes
- **THEN** the profile's parsed total exceeds its returned total so the loss is
  distinguishable from an empty model response
