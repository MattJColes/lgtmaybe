## Why

Automatic diagrams can disappear when a new push cancels a PR's `opened` review: diagram posting is deferred until that review finishes, while the replacement `synchronize` review is explicitly ineligible to post it. This leaves a successfully reviewed PR without the default-on architecture and sequence diagram.

## What Changes

- Keep automatic diagram generation enabled for `synchronize` replacement runs as well as `opened` and `reopened` runs.
- Preserve the existing `auto_diagram` opt-out and idempotent comment upsert behavior.
- Add a concise summary of the PR's changes to the existing structured diagram response and render it above the diagrams in the same comment.
- Add regression coverage for the surviving `synchronize` run posting the diagram.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Automatic diagram eligibility includes PR synchronization events, and diagram comments summarize the change alongside their structure and sequence views.

## Impact

The Action event gate, diagram structured-output model and renderer, their tests and user-facing documentation, and the living CLI specification change. No dependency, provider, or authentication behavior changes.
