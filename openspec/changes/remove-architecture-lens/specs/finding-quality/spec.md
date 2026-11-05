## ADDED Requirements

### Requirement: Reflection drops fixes not worth the churn

Reflection SHALL drop a finding whose fix would leave behaviour, risk and
maintenance cost practically unchanged, such as swapping one acceptable form
for another or guarding a case the shown code rules out. It SHALL NOT drop a
finding on this rule when the finding shows an observable defect.
<!-- anchor: quality.worth-the-churn -->

#### Scenario: a finding proposes an equivalent rewrite
- **WHEN** a finding asks to rename a clear local or restate a working loop as a comprehension
- **THEN** reflection drops it

#### Scenario: a low-severity finding shows a real defect
- **WHEN** a `low` correctness finding carries a failure scenario the diff supports
- **THEN** this rule does not drop it

## MODIFIED Requirements

### Requirement: Self-reflection with a keep-all safe default

After merge/dedupe the provider SHALL audit its own findings and drop the ones
it marks low-confidence, including cross-file false positives whose validity
hinges on unshown code — while gap findings (performance, complexity, intent,
spec) stay valid. Missing-test/doc findings SHALL keep that
protection only when what they rely on is shown; otherwise they are claims
about unshown code like any other. An unparseable audit keeps everything.
<!-- anchor: quality.reflect -->

#### Scenario: reflection output can't be parsed
- **WHEN** the audit reply fails to parse
- **THEN** every finding is kept — a broken audit never silently drops findings

#### Scenario: a lockfile finding backed by a scanner hint
- **WHEN** a finding sits on a lockfile the diff never shows and a scanner hint backs it
- **THEN** the audit gets that hint, redacted and wrapped, as its evidence; with
  no such finding the audit prompt is byte-identical

#### Scenario: the test a finding calls absent lives in an untouched file
- **WHEN** a finding says the change adds no test and no test file is in front of the auditor
- **THEN** the carve-out does not protect it — it is judged as a cross-file absence claim

#### Scenario: the lens fan-out overruns a whole-review ceiling
- **WHEN** lens calls pass `max_review_seconds` or `max_review_tokens`
- **THEN** the audit still runs, because a review that overran still needs
  pruning; only a termination signal skips it

#### Scenario: a large diff exhausts the reflection grounding budget
- **WHEN** reflection defers a finding and the full PR diff leaves no room for fetched code
- **THEN** the recheck retains patches for deferred findings and requested files, excluding unrelated patches so retrieved evidence reaches the auditor

### Requirement: Defect findings earn eligibility with causal evidence

The engine SHALL require a non-blank `failure_scenario` for security,
correctness, deprecation, and performance findings before reflection and SHALL
apply the rule regardless of model-selected severity. Tests, documentation,
complexity, intent, ponytail, and custom-lens findings SHALL remain eligible
without one. A fast-preset finding the model left unattributed carries the
`code-health` group id and SHALL be gated too, since it cannot show it belongs
to an exempt member.
<!-- anchor: quality.failure-scenario -->

#### Scenario: model lowers severity to avoid evidence
- **WHEN** a correctness finding is marked `low` with no failure scenario
- **THEN** the engine drops it before reflection and posting

#### Scenario: gap finding has no runtime failure
- **WHEN** a tests finding has `failure_scenario: null`
- **THEN** it remains eligible for reflection and posting
