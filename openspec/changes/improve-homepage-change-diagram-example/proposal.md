## Why

The homepage's cache example proves that C4 diagrams render, but it undersells
the feature by showing only one small infrastructure addition. A richer example
will let visitors understand at a glance that lgtmaybe can map a change across
multiple containers, relationship types, and system boundaries.

## What Changes

- Replace the homepage's Redis cache diagram with a compact pull-request example
  spanning a user-facing app, API, asynchronous worker, data store, and external
  service.
- Mark both changed and newly added elements in the diagram labels, matching the
  generator's real output contract.
- Keep the existing link to the detailed change-diagram guide and verify the
  rendered Mermaid through the existing docs test and strict MkDocs build.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Make the homepage's representative change diagram demonstrate
  the breadth of supported change mapping rather than only a single dependency
  addition.

## Impact

- Documentation only: the homepage example, its generated `llms-full.txt`
  mirror, the focused homepage test, and this OpenSpec change.
- No runtime, API, dependency, configuration, or compatibility changes.
