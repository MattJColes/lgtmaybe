## Why

The homepage's cache example proves that C4 diagrams render, but it undersells
the feature by showing only one small infrastructure addition. The long feature
overview also delays the more useful commands, diagram, and getting-started
links. A shorter overview and richer example will make the product easier to
scan without hiding what it reviews.

## What Changes

- Replace the homepage's Redis cache diagram with a compact pull-request example
  spanning a user-facing app, API, asynchronous worker, data store, and external
  service.
- Cut the overview before "Start here" to roughly half its current length while
  retaining every review category, the safety model, and the available slash
  commands.
- Mark both changed and newly added elements in the diagram labels, matching the
  generator's real output contract.
- Keep the existing link to the detailed change-diagram guide and verify the
  rendered Mermaid through the existing docs test and strict MkDocs build.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Make the homepage easier to scan while preserving its review
  categories and demonstrating the breadth of supported change mapping.

## Impact

- Documentation only: the homepage example, its generated `llms-full.txt`
  mirror, the focused homepage test, and this OpenSpec change.
- No runtime, API, dependency, configuration, or compatibility changes.
