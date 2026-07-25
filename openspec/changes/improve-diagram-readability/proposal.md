## Why

Generated C4 diagrams can place relationship arrows and labels across component
cards, making the diagram harder to read than the text fallback. Mermaid's C4
renderer uses declaration-order layout and manual label offsets, so arbitrary
model-generated graphs cannot be made reliably readable with the current
contract.

## What Changes

- Generate compact Mermaid flowcharts with automatic edge routing instead of
  Mermaid C4 syntax.
- Keep relationship labels short and put change markers on nodes only.
- Avoid manual styling and positioning directives so diagrams remain readable
  across Mermaid themes and GitHub rendering.
- Add regression coverage for the prompt contract using the overlap-prone
  branched release graph.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: `/diagram` and the local `diagram` command produce a compact,
  automatically laid-out Mermaid change diagram rather than a C4 diagram.

## Impact

The diagram-generation prompt, focused engine tests, and user-facing diagram
documentation change. The structured provider response, Markdown rendering,
ASCII fallback, slash command, GitHub upsert, local command, and dependencies
remain unchanged.
