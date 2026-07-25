## Why

Raising the cloud worker ceiling does not shorten the current default review:
the three fast-preset calls already start together. In the observed dogfood
profile, the correctness call took 513 seconds and emitted 32,768 output tokens
while the other calls completed within 66 seconds. The wall clock is therefore
set by one oversized task, not by queued work.

## What Changes

- On configurations with more than one effective review worker, split the
  default correctness checklist into two focused calls: control/data-flow
  correctness and state/lifecycle correctness.
- Run both calls through the existing bounded fan-out pool and merge their
  findings back into the `correctness` category.
- Keep the current combined correctness call when effective concurrency is one,
  so Ollama and single-slot OpenAI-compatible servers do not pay for another
  serial request.
- Keep the cloud concurrency default at eight and retain the explicit
  `max_concurrency` override.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `review-pipeline`: Make the fast fan-out shape depend on whether the bounded
  executor can run more than one call.
- `prompt-and-lenses`: Split the correctness checklist into two focused prompts
  for parallel-capable reviews while preserving the combined serial prompt.
- `core-contracts`: Define the provider-aware fast-preset call shape.

## Impact

Parallel-capable fast reviews make four calls per batch instead of three, with
the two correctness calls overlapping. Single-worker reviews remain at three
calls. This trades one additional cloud request and repeated input tokens for a
shorter critical path and a narrower output task; full and explicitly selected
category reviews are unchanged.
