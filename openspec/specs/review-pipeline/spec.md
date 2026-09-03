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
- **THEN** the identical request is not re-issued: it fails the same way, at
  cost. A reply that could not be parsed may still be reformatted once by a
  DIFFERENT request (see "An unparseable reply is reformatted once")

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

### Requirement: The batching budget is fitted to the model's window

The engine SHALL cap a defaulted `max_input_tokens` at the prompt budget the
provider reports for its model (feature-detected `input_budget()`; the litellm
adapter reads litellm's model map, or ollama's `num_ctx`, less the output
ceiling it sends), before batching. A value the user configured SHALL be left
alone, a provider with no method or no opinion SHALL change nothing, and the
fit is only ever downward — the default is also a spend ceiling. Without it a
100k batch is refused by a 65k-window model and silently truncated by ollama.
<!-- anchor: engine.input-budget -->

#### Scenario: the model's window is smaller than the default
- **WHEN** `max_input_tokens` is unset and the provider reports a smaller budget
- **THEN** batches are built against the reported budget, and the fit is logged

#### Scenario: the user set a budget
- **WHEN** `max_input_tokens` is configured, even above what the provider reports
- **THEN** the configured value is used unchanged

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
pieces of the same batch rather than re-sent unchanged: one split level, the
pieces reviewed concurrently, the shrink disclosed in the summary. Three budgets
trigger it: the wall clock, the `max_tokens` ceiling an answer hits, and the
context window a prompt is refused by (`ProviderInputTooLarge`, nothing to
salvage). Findings completed before a truncation SHALL be kept; the lens fails.
<!-- anchor: engine.timeout-split -->

#### Scenario: a multi-file batch times out
- **WHEN** a lens call on a batch of several files exceeds its wall-clock budget
- **THEN** the batch is halved by file and each half reviewed in its own call, and
  the summary reports that a batch was shrunk

#### Scenario: a single-file batch times out
- **WHEN** the timed-out batch holds one file
- **THEN** its hunks are divided into two groups, one review call each, so an
  oversized lone file still shrinks

#### Scenario: a call runs past its output ceiling, or its context window
- **WHEN** a lens call's answer stops at the `max_tokens` ceiling, or its prompt
  is refused for the window
- **THEN** the batch is split the same way; findings finished before a cut are kept

#### Scenario: a piece exhausts its budget as well
- **WHEN** a piece of an already-split batch times out or truncates again
- **THEN** it fails as an ordinary failed call naming `max_tokens` — no recursion,
  and no escalation of its own: that is the whole batch's to spend

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
split, and SHALL name both a lower `reasoning_effort` and a higher `max_tokens`
as the levers: a smaller payload does not shrink a thinking budget, so the split
would re-spend the whole `max_tokens` ceiling on every piece and fail
identically — but these counts cannot say whether the thinking expands to fill
any ceiling given it or merely outgrew this one, and those two have opposite
fixes. The decision reads the counts the failure carries, never its message.
Findings completed before the cut are kept on this path too, and a failure that
says nothing about size is not split at all.
<!-- anchor: engine.reasoning-ceiling -->

#### Scenario: the ceiling went on thinking
- **WHEN** a lens call truncates having spent nearly the whole ceiling reasoning
- **THEN** no pieces are reviewed, the salvage is kept, and the notice names both
  levers, since the counts alone cannot say which one is the fix

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

### Requirement: A reasoning-bound truncation is retried once at a lower effort

A lens whose truncation was reasoning-bound SHALL be re-run once with its
`reasoning_effort` stepped down one level, merging its findings with the cut
call's salvage — changing the one variable that can move a thinking budget.
Exactly one attempt, always downward, never on a payload-bound truncation. With
no effort configured the step SHALL be to a named floor, so a model reasoning at
its own default is not the one with no lever; where the route would discard the
override the original failure SHALL be reported instead of an identical request,
and that judgement SHALL fail open. The retry SHALL re-check the deadline, token
budget and interrupt first, and a lens that only answered after stepping down
SHALL be named in the summary.
<!-- anchor: engine.reasoning-step-down -->

#### Scenario: the lower effort fits
- **WHEN** a reasoning-bound truncation is re-run one level down and answers
- **THEN** its findings join the review and the summary names the lens that
  needed the lower setting

#### Scenario: the lower effort truncates too
- **WHEN** the step-down retry is itself reasoning-bound
- **THEN** it reports and stops — one attempt, never a walk down the ladder
#### Scenario: no effort was configured
- **WHEN** a reasoning-bound truncation comes from a run that set no effort
- **THEN** the retry goes out at the floor, in that provider's own effort shape

#### Scenario: the route would discard the override
- **WHEN** a floor would be sent to a route whose capability map omits the param
- **THEN** nothing is retried — the request would go out identical to the one
  that just failed, billed twice for one answer

#### Scenario: there is no step to take
- **WHEN** the configured effort is the lowest rung, or names no position on the
  ladder at all
- **THEN** nothing is retried and nothing is spent

#### Scenario: a ceiling was reached while the first call was finishing
- **WHEN** the deadline, token budget or a termination signal lands first
- **THEN** the retry is not issued and the truncation is reported as it stands

### Requirement: A truncation escalates to a second model only as a last resort

A truncated lens SHALL be re-run once on `fallback_model` only after the remedy
its token counts named has been tried on the primary and failed — a smaller
payload for a payload-bound truncation, a lower `reasoning_effort` for a
reasoning-bound one. Switching model says nothing about the failure: it re-sends
the same request at the same ceiling, so it is last. Exactly one attempt, spent
by the whole batch and never by each piece, skipped with no fallback configured,
and re-checking the deadline, token budget and interrupt first. Lens calls SHALL
therefore hand a truncation back rather than let the adapter switch model
beneath them. A lens a second model answered SHALL be named in the summary
alongside that model, however the switch happened.
<!-- anchor: engine.model-escalation -->

#### Scenario: the aimed remedy runs first
- **WHEN** a lens truncates and a fallback model is configured
- **THEN** the split or the step-down is attempted on the primary, and the
  fallback is reached only if that attempt failed as well

#### Scenario: the fallback answers
- **WHEN** the escalated call parses
- **THEN** its findings join the review, merged with the cut call's salvage, and
  the summary names the lens and the model that answered it

#### Scenario: the fallback truncates too
- **WHEN** the second model runs out of output tokens as well
- **THEN** it reports and stops — one second model, never a walk down a roster

#### Scenario: a split's pieces all failed
- **WHEN** every piece of a shrunk batch failed
- **THEN** the batch buys ONE escalation, not one per piece

#### Scenario: no fallback is configured
- **WHEN** the run names no second model
- **THEN** nothing is escalated and the requests are byte-identical to before

#### Scenario: the adapter switched model by itself
- **WHEN** a non-truncation failure was rescued by the adapter's own fallback
- **THEN** the summary discloses it too, read off the model that answered

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
