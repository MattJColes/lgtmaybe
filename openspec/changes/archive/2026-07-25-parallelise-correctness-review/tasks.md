## 1. Acceptance Tests

- [x] 1.1 Add a failing test that a parallel-capable fast review builds separate
  `correctness-flow` and `correctness-state` tasks and runs them concurrently.
- [x] 1.2 Add a failing test that an auto-configured Ollama review and an
  explicit `max_concurrency: 1` review keep one combined correctness task.
- [x] 1.3 Pin both correctness tasks' findings to the public `correctness`
  category and verify cross-task duplicates collapse.

## 2. Focused Parallelism

- [x] 2.1 Split the correctness checklist into focused flow and state prompt
  sections while retaining the current combined form.
- [x] 2.2 Select the split from effective provider concurrency and feed both
  tasks through the existing global fan-out pool.
- [x] 2.3 Update maintained configuration/reference documentation with the
  provider-aware fast-preset call shape.

## 3. Verification

- [x] 3.1 Run focused preset, prompt, concurrency, and eval tests.
- [x] 3.2 Run the full test, lint, type, OpenSpec, anchor, drift, and docs gates.
- [x] 3.3 Compare the dogfood profile's correctness critical path and total
  tokens before deciding whether the split should ship.
  The recorded pre-split straggler took 513 seconds and emitted 32,768 output
  tokens. Production dogfood run 30154965187 split the work into
  `correctness-flow` (97.37 seconds) and `correctness-state` (0.81 seconds);
  together they used 8,907 input and 3,885 output tokens. The diffs were not
  identical, so this is directional rather than a controlled A/B result, but
  it shows no regression and supports keeping the split enabled.
