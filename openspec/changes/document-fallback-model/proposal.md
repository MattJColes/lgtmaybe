## Why

`fallback_model` is listed in the generated configuration reference and the
GitHub Action inputs, but the configuration guide does not explain when it
runs, which provider it uses, or what happens when it also fails. Readers
cannot predict the extra calls or choose a useful model from the current
fragments.

## What Changes

- Add a `fallback_model` section to the existing `.lgtmaybe.yml` guide.
- Document configuration precedence, provider scope, recovery order, cost,
  reporting, and terminal failure behavior.
- Expand the cost guide's short recommendation and link it to the detailed
  configuration section.
- Regenerate the combined LLM documentation and verify the MkDocs build.

## Capabilities

### New Capabilities

None. This change documents existing behavior.

### Modified Capabilities

None. This is a documentation-only change, so `.openspec.yaml` opts out of
spec deltas.

## Impact

The change affects the configuration and cost guides plus generated
documentation. It changes no runtime behavior, APIs, dependencies, or living
specifications.
