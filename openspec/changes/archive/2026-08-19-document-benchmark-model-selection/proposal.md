## Why

People choosing a review model currently get generic advice instead of evidence from lgtmaybe's benchmark corpus. The benchmark repository now contains breadth and long-horizon results that can support a practical recommendation while making the limits of those results clear.

## What Changes

- Add a focused model-selection guide that translates benchmark results into recommendations for balanced quality, long-diff recall, lower noise, and local/private operation.
- Replace the README's generic model advice with a concise benchmark-backed cloud-versus-local decision and a link to the full guide.
- Add the guide to the rendered documentation navigation and source index.
- Cite the benchmark repository as the source of truth rather than copying its full leaderboard, so detailed results and methodology remain maintainable in one place.
- Verify links, the MkDocs build, and OpenSpec/spec-anchor checks.

## Capabilities

### New Capabilities

None. This change documents existing benchmark evidence and does not alter product behavior.

### Modified Capabilities

None. This is a documentation-only change, so `.openspec.yaml` opts out of spec deltas.

## Impact

The change affects `README.md`, the documentation navigation/index, and one new how-to guide under `docs/how-to/`. It changes no APIs, runtime behavior, dependencies, or living specifications. Benchmark numbers remain owned by `MattJColes/lgtmaybe-benchmarks` and are dated/version-qualified in the guide.
