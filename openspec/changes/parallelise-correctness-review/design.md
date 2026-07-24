## Context

The engine already flattens every `(batch, lens)` task into one bounded thread
pool. The default fast preset now contributes only three tasks per batch:
security, correctness (with intent), and code health. Increasing the pool above
three cannot improve a one-batch review.

The production straggler is the correctness task itself. It owns ten distinct
bug classes and can spend far more reasoning/output tokens than the other
lenses. Decomposing that task creates useful parallel work; increasing the
executor size does not.

## Goals / Non-Goals

**Goals:**

- Shorten the fast review's critical path on parallel-capable providers.
- Keep all existing correctness and intent coverage.
- Preserve one global concurrency bound and deterministic merge order.
- Avoid increasing serial-provider latency or request count.

**Non-Goals:**

- Raise the default cloud concurrency above eight.
- Parallelise reflection, which depends on the merged review findings.
- Change the full preset or explicitly selected categories.
- Add another concurrency setting or dependency.

## Decisions

1. Split correctness into `correctness-flow` (nulls, boundaries, ranges, error
   paths, conditionals, numeric/validation errors) and `correctness-state`
   (resource ordering, races/async, time, and aliasing/mutation).
2. Fold stated-intent checking into `correctness-flow` only, so PR-authored
   prose still reaches one review call rather than widening its injection and
   token surface.
3. Attribute findings from both tasks to the existing `correctness` category.
   Their distinct task labels remain visible in profiling.
4. Select the split only when the configured effective concurrency can exceed
   one: cloud auto mode or an explicit `max_concurrency > 1`. An explicit cap
   of one and single-stream provider defaults keep the combined prompt.
5. Reuse the current `_fan_out` executor, ordering, deadline, cache warm-up, and
   dedupe behavior. No second pool or unbounded task submission is introduced.

## Risks / Trade-offs

- The parallel path repeats the diff in one additional request. Prompt caching
  offsets this where supported; elsewhere the speed-up costs more input tokens.
- Two focused calls may overlap in what they flag. Existing deterministic
  dedupe removes duplicate comments.
- A provider may still make either task a straggler. The dogfood timing profile
  will show both labels separately so the split can be tightened or reverted
  using evidence.
- Provider-aware call counts are less uniform. Tests will pin both the
  parallel-capable and single-worker paths.
