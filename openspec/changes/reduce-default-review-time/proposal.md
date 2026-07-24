## Why

A medium pull request can exhaust the dogfood workflow's 20-minute job limit.
In the observed run, three review calls finished within 57 seconds while one
provider call ignored the configured timeout and remained in flight until
GitHub cancelled the job. The everyday preset also spends a separate call on
tests and documentation, which has shown high latency and low yield.

## What Changes

- Reduce the default `fast` preset from four review calls to three by reserving
  the tests and documentation lenses for `full` or explicit category runs.
- Add a local wall-clock guard around each LiteLLM request so a provider call
  cannot outlive its configured timeout even when the downstream client hangs.
- Keep `full` and explicit categories as the opt-in paths for deeper scans.
- Keep automatic C4 diagrams enabled in every supplied starter workflow and
  the dogfood workflow.
- Enable the existing timing profile in the dogfood workflow so future slow
  runs retain a stage and call breakdown.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `review-pipeline`: Define the narrower everyday preset.
- `prompt-and-lenses`: Reserve tests and documentation for deep or explicit
  reviews.
- `core-contracts`: Describe the three-call default and nine-lens full preset.
- `provider-gateway`: Enforce the configured provider-call timeout.
- `cli-and-local`: Preserve automatic C4 diagrams in supplied workflows while
  documenting the faster default review.

## Impact

Default reviews make one fewer model call and no longer inspect missing tests
or documentation unless the user selects `full` or those categories
explicitly. Security, correctness, intent, performance, complexity, ponytail,
and deprecation coverage remain on. One timed-out provider request may leave a
daemon transport thread to finish in the background, but it no longer prevents
the CLI process or GitHub job from completing.
