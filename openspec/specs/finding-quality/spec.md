# finding-quality Specification

## Purpose

The passes between raw lens output and what gets posted: suppression,
self-reflection with cross-file humility (`engine/reflect.py`), confidence
scoring, cross-file symbol resolution for deferred verdicts, and declarative
finding rules — all biased to never silently drop a real finding.
## Requirements
### Requirement: Self-reflection with a keep-all safe default

After merge/dedupe the provider SHALL audit its own findings and drop the ones
it marks low-confidence, including cross-file false positives whose validity
hinges on unshown code — while gap findings (performance, complexity, intent,
spec) stay valid. A missing-test/doc finding SHALL keep that protection only
when the test or doc file is shown or retrieved; otherwise it is an absence
claim about unshown code like any other. An unparseable audit keeps everything.
<!-- anchor: quality.reflect -->

#### Scenario: reflection output can't be parsed
- **WHEN** the audit reply fails to parse
- **THEN** every finding is kept — a broken audit never silently drops findings

#### Scenario: the test a finding calls absent lives in an untouched file
- **WHEN** a finding says the change adds no test and no test file is in front of the auditor
- **THEN** the carve-out does not protect it — it is judged as a cross-file absence claim

#### Scenario: the lens fan-out overruns a whole-review ceiling
- **WHEN** lens calls pass `max_review_seconds` or `max_review_tokens`
- **THEN** the audit still runs, because a review that overran still needs
  pruning; only a termination signal skips it

### Requirement: One lens cannot flood a review

A single (batch, lens) call SHALL contribute at most `max_findings_per_lens`
findings, keeping the highest severity first. A model under structured output can
restate one claim across every line it sees, and location dedupe does not collapse
those restatements because each carries a distinct line. When the bound fires the
summary SHALL name the lens and the number dropped; `0` SHALL disable it.
<!-- anchor: quality.lens-bound -->

#### Scenario: a lens returns far more findings than the bound
- **WHEN** one lens call returns more than `max_findings_per_lens` findings
- **THEN** the most severe are kept and the summary names the lens and the count
  dropped, so the truncation is visible

#### Scenario: an ordinary lens result
- **WHEN** a lens returns fewer findings than the bound
- **THEN** every one is kept and no notice is raised

### Requirement: Verdicts are lenient to read, strict to act on

Each kept verdict SHALL carry a 0-10 confidence score (the auditor tries to
disprove the finding to reach it); `min_confidence` drops findings scored
below it, and an unscored kept finding survives any threshold.
<!-- anchor: quality.verdicts -->

#### Scenario: kept finding has no score
- **WHEN** a verdict keeps a finding but omits `confidence`
- **THEN** the finding survives even a `min_confidence` of 10

### Requirement: Suppressions apply before reflection

Known-fine findings SHALL be dropped before the reflection pass — ignored
fingerprints and inline suppression comments — so audit budget is never spent
on them.
<!-- anchor: quality.suppress -->

#### Scenario: a finding was previously dismissed
- **WHEN** its fingerprint is in `ignore_fingerprints`
- **THEN** it never reaches reflection or posting

### Requirement: Downvoted findings are learned and suppressed

When `learn_feedback` is on, a finding an authorised reviewer reacted 👎 to SHALL
be suppressed on the next run — its fingerprint is read from GitHub each run and
carried on `PRContext.feedback_downvotes` into the suppression pass. A high or
critical security finding is never suppressed this way, so a downvote can never
hide a serious vulnerability. Best-effort: a gateway without the capability or
any read error leaves the review untouched, never failing.
<!-- anchor: quality.learned-feedback -->

#### Scenario: a finding was downvoted last run
- **WHEN** the gateway reports an authorised reviewer's 👎 for its fingerprint
- **THEN** it is dropped before reflection, unless it is a high/critical security finding

### Requirement: Finding rules are declarative, never code

`finding_rules` SHALL be an ordered declarative match (path glob / lens
category / title / severity floor, ANDed) to action (`drop` / `set_severity`),
applied just before posting. Deliberately not a hook: no user code ever runs.
<!-- anchor: quality.rules -->

#### Scenario: a team downgrades a lens on a path
- **WHEN** a rule matches `category: complexity` under `legacy/**`
- **THEN** the action applies with no user code executed

### Requirement: Deferred verdicts resolve real definitions

A deferred cross-file symbol SHALL be located structurally with ast-grep in a
read-only corpus of the PR's base branch (never executed), so the auditor
re-judges against the real definition instead of guessing. Unsupported
languages or any ast-grep failure fall back to the plain verdict.
<!-- anchor: quality.symbol-resolution -->

#### Scenario: auditor names a symbol outside the diff
- **WHEN** a verdict needs `validate_tenant` defined in an unshown file
- **THEN** ast-grep finds its file in the corpus and the auditor re-judges
  against the actual code

### Requirement: Defect findings earn eligibility with causal evidence

The engine SHALL require a non-blank `failure_scenario` for security,
correctness, deprecation, and performance findings before reflection and SHALL
apply the rule regardless of model-selected severity. Tests, documentation,
complexity, intent, ponytail, and custom-lens findings SHALL remain eligible
without one.
<!-- anchor: quality.failure-scenario -->

#### Scenario: model lowers severity to avoid evidence
- **WHEN** a correctness finding is marked `low` with no failure scenario
- **THEN** the engine drops it before reflection and posting

#### Scenario: gap finding has no runtime failure
- **WHEN** a tests finding has `failure_scenario: null`
- **THEN** it remains eligible for reflection and posting

### Requirement: Reflection validates claimed failure scenarios

When reflection is enabled, the auditor SHALL drop a defect finding whose
failure scenario is speculative, contradicted by the diff or grounded file
text, or depends on an unsupported causal step. The existing `--no-reflect`
override and keep-all audit-error fallback SHALL remain unchanged.
<!-- anchor: quality.failure-validation -->

#### Scenario: scenario contradicts grounded code
- **WHEN** the auditor can disprove a claimed failure using the diff or fetched
  file context
- **THEN** its verdict drops the finding

#### Scenario: reflection is explicitly disabled
- **WHEN** `--no-reflect` is used
- **THEN** the presence gate still applies but semantic validation is skipped
