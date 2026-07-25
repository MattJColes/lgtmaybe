## MODIFIED Requirements

### Requirement: Fast preset splits correctness only when it can overlap

The default `fast` preset SHALL run security and code-health tasks plus
provider-aware correctness tasks. A parallel-capable configuration SHALL split
correctness into focused flow and state/lifecycle calls, both attributed to the
`correctness` category; a single-worker configuration SHALL keep one combined
correctness call. Stated intent SHALL remain attached to one correctness task.
Tests and documentation SHALL remain reserved for `full` or explicit category
reviews.
<!-- anchor: prompt.groups -->

#### Scenario: cloud default
- **WHEN** `fast` uses a cloud provider with auto-concurrency
- **THEN** its four calls are security, correctness-flow, correctness-state,
  and code health

#### Scenario: local single-slot default
- **WHEN** `fast` uses Ollama with auto-concurrency
- **THEN** its three calls are security, combined correctness, and code health
