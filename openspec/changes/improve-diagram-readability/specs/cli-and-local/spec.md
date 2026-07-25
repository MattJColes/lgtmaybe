## MODIFIED Requirements

### Requirement: Slash commands route to the same engine
<!-- anchor: cli.slash -->

`issue_comment` events SHALL parse into commands — `/review` (with `full`
forcing a full re-review), `/improve`, `/ask <q>` replying in-thread,
`/describe` upserting a structured description, and `/diagram` upserting a
compact, automatically laid-out Mermaid change diagram — all dispatched to the
same engine and provider stack.

#### Scenario: reviewer comments /review full
- **WHEN** the comment body is `/review full`
- **THEN** a full review runs, bypassing triage and incremental scoping

#### Scenario: reviewer comments /diagram
- **WHEN** the comment body is `/diagram`
- **THEN** a Mermaid flowchart with automatic edge routing is upserted as its
  own comment, with compact cards, short relationship labels, and change
  markers on nodes rather than arrows

#### Scenario: provider returns legacy C4
- **WHEN** the diagram provider ignores the flowchart contract and returns C4
  source together with an ASCII rendering
- **THEN** the C4 source is not posted as Mermaid and the ASCII rendering is
  shown instead
