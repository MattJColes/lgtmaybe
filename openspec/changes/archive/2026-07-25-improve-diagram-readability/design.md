## Context

`engine/diagram.py` currently asks the provider for Mermaid C4 source and
accepts both C4 and flowchart prefixes. Mermaid's C4 renderer does not
automatically lay out a graph: card positions follow declaration order and
relationship labels need manual per-edge offsets. That is brittle for
model-generated topology and produced a release graph whose arrows and labels
crossed component cards.

The same graph rendered as `flowchart LR` uses Mermaid's automatic graph layout
and remained readable without styling or positional directives. The existing
structured response, Markdown renderer, and ASCII fallback already support this
source format.

## Goals / Non-Goals

**Goals:**

- Make generated Mermaid diagrams use automatic graph layout.
- Keep cards and relationship labels compact enough for GitHub comments.
- Fail back to the existing ASCII rendering when a provider returns legacy C4
  or otherwise unsupported Mermaid.
- Preserve the current provider call, response schema, security boundaries, and
  posting behavior.

**Non-Goals:**

- Render Mermaid in Python or add a Mermaid dependency.
- Post-process arbitrary model-generated graphs into a new topology.
- Add user-configurable themes, layout engines, or diagram formats.
- Change the ASCII fallback.

## Decisions

### Generate a plain left-to-right flowchart

The system prompt will require `flowchart LR`, a maximum of six nodes, short
relationship labels, and change markers on node descriptions only. It will
forbid manual styling and positioning directives.

Alternative: tune C4 declaration order and `UpdateRelStyle` offsets. Rejected
because every generated graph would need different hand-chosen offsets and C4
styling has poor dark-theme contrast.

Alternative: add a renderer and inspect SVG geometry. Rejected because it adds
a dependency and a repair loop for a problem Mermaid's normal flowchart layout
already solves.

### Accept automatic flowcharts only

The cheap Mermaid prefix check will accept `flowchart` and its `graph` alias,
but no longer accept C4 diagram prefixes. A provider that ignores the prompt and
returns C4 will therefore show the already-generated ASCII fallback instead of
posting an overlap-prone rendered diagram.

Alternative: continue accepting C4 for backward compatibility. Rejected
because model output is ephemeral and accepting it preserves the unreadable
failure mode this change removes.

### Teach the topology with one branched worked example

The prompt example will use a small branched release graph that mirrors the
reproduced failure shape. It demonstrates automatic routing, compact multiline
cards, short edge labels, and status markers on cards without coupling the
generator to a specific repository.

## Risks / Trade-offs

- [A six-node linear graph may render wide] → Keep card text compact; Mermaid
  scales and routes the graph, while the ASCII fallback remains available.
- [A weak provider may still return C4] → Reject C4 at the prefix check and show
  the ASCII fallback.
- [Prompt constraints do not prove visual geometry] → Lock the controllable
  contract with tests and keep the reproduced Mermaid Live comparison as the
  design evidence; do not add a rendering dependency.
