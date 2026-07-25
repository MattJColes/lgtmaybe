## Why

Auxiliary model calls can currently escape their task contract: malformed model-authored Mermaid passes a prefix-only check, while `/ask` posts any provider text verbatim, including review-shaped JSON. These rare failures create broken or confusing PR comments at the user-facing boundary.

## What Changes

- Ask the diagram model for typed nodes and edges instead of raw Mermaid and ASCII strings.
- Render Mermaid and its text fallback deterministically in lgtmaybe, with safe labels, stable node ids, and invalid-edge filtering.
- Give `/ask` a task-specific structured answer schema and reject wrong-schema JSON instead of posting it verbatim.
- Add focused regression tests for the reported Mermaid parse failure and `{"findings": []}` answer leak.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Diagram and `/ask` outputs become task-specific structured data that lgtmaybe validates and renders before posting.

## Impact

The change affects the auxiliary result models, diagram prompt/renderer, `/ask` provider call, slash-command tests, and the `cli-and-local` living spec. It adds no dependency and does not change the public CLI or Action inputs.
