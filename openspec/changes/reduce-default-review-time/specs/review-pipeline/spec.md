## MODIFIED Requirements

### Requirement: Per-lens fan-out through one bounded executor
<!-- anchor: engine.fan-out -->

Every (batch, lens) call SHALL run through one global bounded executor sized by
`max_concurrency` (auto: 8 cloud, 1 for ollama/openai-compatible). The default
`fast` preset SHALL run three calls per batch: security, correctness with
stated intent, and code health. The `full` preset and explicit categories SHALL
retain tests and documentation coverage.

#### Scenario: default medium pull request
- **WHEN** a review uses the default `fast` preset
- **THEN** each batch makes three review calls and does not run the tests or
  documentation lenses

#### Scenario: deep audit
- **WHEN** a review uses the `full` preset
- **THEN** every built-in category runs, including tests and documentation
