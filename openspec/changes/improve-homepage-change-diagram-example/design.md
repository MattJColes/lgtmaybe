## Context

The homepage currently uses the generator prompt's minimal Redis cache example.
It contains four nodes and marks only one dependency as new. The overview before
"Start here" is also 694 source words, much of it spent on long lists of examples
for each review category. The page is technically complete, but readers have to
work through too much detail before reaching the commands, diagram, and setup
links.

## Goals / Non-Goals

**Goals:**

- Demonstrate the breadth of a realistic change diagram at homepage scale.
- Keep the overview under 400 source words while retaining every review category
  and trust-boundary feature.
- Use only Mermaid C4 syntax and change labels supported by the current output
  contract.
- Keep the example readable on the documentation site's mobile and desktop
  layouts.

**Non-Goals:**

- Change diagram generation, prompting, or rendering.
- Redesign the homepage or add a binary screenshot.
- Replace the smaller tutorial example in the detailed how-to guide.

## Decisions

- Show an asynchronous order-confirmation change: customer and storefront feed
  an order API, which stores the order and publishes to a new queue; a new worker
  calls an external email provider. This demonstrates people, containers, a
  database, an external boundary, and synchronous and asynchronous relationships
  in one plausible pull request.
- Mark the API as `(changed)` and the queue and worker as `(new)`. This mirrors
  the generator contract and makes the diff's impact understandable without
  relying on colour.
- Group related review categories into five short bullets instead of listing
  every possible bug class. Keep the detailed examples in "What gets reviewed",
  where readers expect depth.
- Name all five slash commands in one plain paragraph immediately before the
  diagram so the interactive features remain visible.
- Keep the source as inline Mermaid. The existing MkDocs renderer provides the
  actual visual output, keeps the example accessible as text, and avoids a new
  asset or dependency.

## Risks / Trade-offs

- [The richer graph is taller than the current example] → Limit it to the
  touched containers and their immediate collaborators.
- [The homepage and how-to examples no longer match] → Treat the homepage as the
  showcase and retain the how-to's smaller example for explanation; both follow
  the same output contract.
- [Shorter copy can hide capability] → Assert the category names and a 400-word
  overview ceiling in the focused homepage test.
