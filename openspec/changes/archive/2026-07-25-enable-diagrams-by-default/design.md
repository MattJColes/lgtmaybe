## Context

`auto_diagram` is intentionally opt-in at the Action API boundary. The
repository's own workflow and the copyable workflow examples omit that input,
so new repositories inherit the disabled behaviour even though diagrams are a
promoted feature.

## Goals / Non-Goals

**Goals:**

- Make automatic diagrams the default experience in workflows supplied for
  new repositories.
- Enable the same experience in lgtmaybe's dogfood workflow.
- Keep examples consistent and testable.

**Non-Goals:**

- Changing the `auto_diagram` Action input default for existing consumers.
- Generating diagrams on `synchronize` events.
- Changing diagram generation or GitHub posting logic.

## Decisions

- Set `auto_diagram: true` explicitly in the dogfood and starter workflows.
  This makes the onboarding choice visible and avoids silently changing
  existing installations through the Action input default.
- Update existing workflow examples rather than introduce a new template or
  helper.
- Extend the existing workflow/config documentation checks where practical so
  future examples do not regress to omitting the input.

## Risks / Trade-offs

- Extra model usage on opened and reopened pull requests → Keep the opt-in
  explicit in workflow YAML so users can remove it.
- Workflow examples may drift apart → Verify every supplied posting-provider
  example carries the same setting.
