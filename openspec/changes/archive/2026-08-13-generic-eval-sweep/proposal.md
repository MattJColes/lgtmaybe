## Why

The A/B harness duplicates one branch per configuration axis, while `evals.rlm` duplicates the whole harness for the `recursive` field.

## What Changes

- Add a generic `--sweep field=value,...` axis to `evals.ab`.
- Remove the preset/context-specific sweep branches and standalone RLM harness.
- Repoint the RLM workflow and contributor documentation at the generic sweep.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is developer tooling, so `skip_specs` is enabled.

## Impact

Evaluation commands, tests, workflow, and contributor docs change; runtime review behaviour does not.
