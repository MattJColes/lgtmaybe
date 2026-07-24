## Why

The docs homepage mentions C4 change diagrams but does not show one, so visitors
cannot immediately see the feature's most visual output. Showing the existing
example on the homepage makes the capability concrete without adding a new
concept or maintaining a second example.

## What Changes

- Add the existing Redis/User API C4 Mermaid example to the main docs homepage.
- Place the example beside the homepage's change-diagram introduction and retain
  the link to the full how-to guide.
- Verify the docs site builds and the Mermaid block renders through the existing
  MkDocs configuration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Document the existing change-diagram output directly on the
  main docs homepage.

## Impact

- Documentation only: `docs/index.md` and its generated `docs/llms-full.txt`
  mirror, with a focused test under `tests/docs/`.
- No runtime, API, dependency, configuration, or compatibility changes.
