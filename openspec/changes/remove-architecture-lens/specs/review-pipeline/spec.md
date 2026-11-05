## MODIFIED Requirements

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
- **THEN** the security, correctness, code-health, and artefacts calls share the bounded executor and may overlap

#### Scenario: single-worker review
- **WHEN** a review sets `max_concurrency: 1` — a very slow local model, or a
  server whose own parallelism is one and whose calls must not queue
- **THEN** the same four calls run within the single-worker pool, serially

#### Scenario: deep audit
- **WHEN** a review uses the `full` preset
- **THEN** every built-in category runs, including tests and documentation
