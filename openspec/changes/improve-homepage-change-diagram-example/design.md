## Context

The homepage currently uses the generator prompt's minimal Redis cache example.
It contains four nodes and marks only one dependency as new. The example is
technically correct, but it does not show how a change diagram can connect
changed application code, new asynchronous infrastructure, an external service,
and unchanged immediate collaborators in one reviewable map.

## Goals / Non-Goals

**Goals:**

- Demonstrate the breadth of a realistic change diagram at homepage scale.
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
- Keep the source as inline Mermaid. The existing MkDocs renderer provides the
  actual visual output, keeps the example accessible as text, and avoids a new
  asset or dependency.

## Risks / Trade-offs

- [The richer graph is taller than the current example] → Limit it to the
  touched containers and their immediate collaborators.
- [The homepage and how-to examples no longer match] → Treat the homepage as the
  showcase and retain the how-to's smaller example for explanation; both follow
  the same output contract.
