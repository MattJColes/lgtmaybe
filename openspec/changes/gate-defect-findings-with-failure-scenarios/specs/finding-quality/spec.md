## ADDED Requirements

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
