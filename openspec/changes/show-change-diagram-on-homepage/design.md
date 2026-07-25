## Context

The homepage currently links to the change-diagram how-to but only describes
the output in prose. The how-to already contains a representative Mermaid C4
example for a Redis cache added in front of a user service.

## Goals / Non-Goals

**Goals:**

- Make the change-diagram feature visually understandable from the homepage.
- Keep one canonical example by reusing the existing Mermaid source.
- Preserve the route from the homepage to the detailed how-to.

**Non-Goals:**

- Change diagram generation, prompts, or rendering.
- Add screenshots or other binary documentation assets.
- Redesign the rest of the homepage.

## Decisions

- Place a Mermaid C4 block immediately after the paragraph that introduces
  `/diagram`. This keeps the example attached to its explanation.
- Reuse the how-to's Redis/User API example verbatim. A new example would add
  maintenance cost and risk the two pages describing different output.
- Render through the existing Mermaid support in MkDocs. A screenshot would be
  less accessible, harder to update, and would not demonstrate native rendering.

## Risks / Trade-offs

- [The diagram adds vertical length near the top of the homepage] → Keep the
  example compact and omit the how-to page's ASCII fallback from the homepage.
- [The duplicate Mermaid source can drift] → Use the same small example and
  cover its presence with the existing docs verification approach.
