# budget-and-truncation Specification

## Purpose

How the engine (`engine/engine.py`) keeps each model call inside the
model's limits: the batching budget fitted to the model's window, the
hunk-by-hunk walk of an over-budget file, and the truncation ladder that
remedies a cut-off answer on the user's own model before it escalates to
`fallback_model`. Every rung degrades loudly, never silently.

## Requirements

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
  is refused for the window (which alone may also slice inside a lone hunk)
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

### Requirement: A truncation is remedied however it was detected

A response cut off mid-answer SHALL enter the same remedy ladder whether the
adapter raised on it or the parser found its body unterminated. Which detector
fires is a fact about what the route reports, not about the failure: a route
that declares `length`, or spends a ceiling we set, is caught by the adapter,
while one whose finish reason litellm cannot map arrives looking clean and is
caught only by the JSON never closing. Detected the second way the lens used to
be lost outright, so an identical cut cost a whole lens on one model and merely
a slower review on another. The salvage, the reasoning-bound decision, the
split, the effort step-down and the model escalation SHALL be the same on both
paths.
<!-- anchor: engine.truncation-detection -->

#### Scenario: the route declares the cut
- **WHEN** a lens call fails with a truncation the adapter raised
- **THEN** the batch is split and its pieces reviewed

#### Scenario: only the unterminated body shows the cut
- **WHEN** a lens call returns a body that stops mid-object while its route
  reports a clean finish and spends no ceiling
- **THEN** the same split runs, and findings completed before the cut are kept

#### Scenario: the split covers the batch
- **WHEN** the pieces of a parser-detected cut are all reviewed
- **THEN** the lens counts as answered and no incomplete notice fires

#### Scenario: a cut the route called clean names no ceiling
- **WHEN** such a truncation is reported
- **THEN** the notice SHALL NOT claim a `max_tokens` hit, because nothing
  measured one — it names the tokens written before the body stopped instead

### Requirement: A split is only attempted when covering less can help

A truncation that spent essentially the whole ceiling reasoning SHALL NOT be
split, and SHALL name both a lower `reasoning_effort` and a higher `max_tokens`
as the levers: a smaller payload does not shrink a thinking budget, so the split
would re-spend the whole `max_tokens` ceiling on every piece and fail
identically — but these counts cannot say whether the thinking expands to fill
any ceiling given it or merely outgrew this one, and those two have opposite
fixes. The decision reads the two counts, never any message, and the ceiling it
divides by is the one the request carried, not however much the model wrote.
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
`reasoning_effort` stepped down to the nearest lower level the model accepts
(per litellm's map; any rung where it is silent), merging its findings with
the cut call's salvage. Exactly one attempt, always downward, never on a
payload-bound truncation. With no effort configured the step SHALL be to a named
floor, kept strictly below the model's default where the map names one; where
the route would discard the override the original failure SHALL be reported
instead of an identical request, and every such judgement SHALL fail open. The
retry SHALL re-check the deadline, token budget and interrupt first, and a lens
that only answered after stepping down SHALL be named in the summary.
<!-- anchor: engine.reasoning-step-down -->

#### Scenario: the lower effort fits
- **WHEN** a reasoning-bound truncation is re-run one level down and answers
- **THEN** its findings join the review and the summary names the lens that
  needed the lower setting

#### Scenario: the lower effort truncates too
- **WHEN** the step-down retry is itself reasoning-bound
- **THEN** no further graded step is taken — the one rung left is off (below)
#### Scenario: no effort was configured
- **WHEN** a reasoning-bound truncation comes from a run that set no effort
- **THEN** the retry goes out at the floor, in that provider's own effort shape

#### Scenario: the route would discard the override
- **WHEN** a floor would be sent to a route whose capability map omits the param
- **THEN** nothing is retried — the request would go out identical to the one
  that just failed, billed twice for one answer

#### Scenario: there is no step to take
- **WHEN** no level the model accepts sits below the configured effort (or its
  default), or the effort names no position on the ladder at all
- **THEN** nothing is retried and nothing is spent

#### Scenario: a ceiling was reached while the first call was finishing
- **WHEN** the deadline, token budget or a termination signal lands first
- **THEN** the retry is not issued and the truncation is reported as it stands

### Requirement: A step-down that is itself reasoning-bound is retried once with reasoning off

A step-down retry that spends its ceiling on reasoning again SHALL be re-run
exactly once with reasoning switched off, before any change of model, and only
where the provider can express "off" in its own shape and litellm's map does not
declare a level set without it. Measured on GLM 5.3
through OpenRouter: `low` and `minimal` both spent a 12,288-token ceiling on
thought in 10 retries of 11, and the fallback model then did the same at ten
times the price — that model treats effort as a switch, and the graded step
lands nowhere. The off retry SHALL NOT be sent when the step-down already was
off, when the step-down cut for a reason other than reasoning, or past the
deadline, token budget or interrupt. A lens that only answered with reasoning
off SHALL be named in the summary as such, apart from lenses that answered at a
lower setting, because it is a different claim about the findings.
<!-- anchor: engine.reasoning-off -->

#### Scenario: off answers where the dial did not
- **WHEN** the step-down retry is reasoning-bound and the route can switch off
- **THEN** one more call goes out with reasoning off, its findings join the
  review, and the summary names the lens as answered without thinking

#### Scenario: off truncates too
- **WHEN** the reasoning-off retry is cut as well
- **THEN** it reports and stops on the primary; the model escalation, if
  configured, is the next and last rung

#### Scenario: the step-down already was off
- **WHEN** a configured `minimal` steps to `none` and that cut is reasoning-bound
- **THEN** nothing more is sent on the primary — a second call at `none` would
  be the request that just failed, billed twice

#### Scenario: the step-down cut for another reason
- **WHEN** the step-down retry's answer, not its thinking, outgrew the ceiling
- **THEN** the off switch is not tried, because thinking less cannot help

### Requirement: A truncation escalates to a second model only as a last resort

A truncated lens SHALL be re-run once on `fallback_model` only after the remedy
its token counts named has been tried on the primary and failed — a smaller
payload for a payload-bound truncation, a lower `reasoning_effort` and then
reasoning off for a reasoning-bound one. Switching model says nothing about the failure: it re-sends
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
