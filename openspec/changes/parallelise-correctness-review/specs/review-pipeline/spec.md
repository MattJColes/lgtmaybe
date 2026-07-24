## MODIFIED Requirements

### Requirement: Per-lens fan-out through one bounded executor

Every `(batch, lens)` call SHALL run through one global bounded executor sized
by `max_concurrency` (auto: eight cloud, one for Ollama/OpenAI-compatible).
When the effective executor can run more than one call, the default fast preset
SHALL submit separate correctness-flow and correctness-state tasks; when it is
single-worker, it SHALL submit the existing combined correctness task.
<!-- anchor: engine.fan-out -->

#### Scenario: parallel-capable default review
- **WHEN** a fast review uses cloud auto-concurrency
- **THEN** security, correctness-flow, correctness-state, and code-health calls
  share the existing bounded executor and may overlap

#### Scenario: single-worker default review
- **WHEN** a fast review uses Ollama auto-concurrency or `max_concurrency: 1`
- **THEN** security, combined correctness, and code-health run within the
  single-worker pool without an additional serial request
