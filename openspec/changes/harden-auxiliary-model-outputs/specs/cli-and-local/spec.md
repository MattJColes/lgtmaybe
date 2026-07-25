## MODIFIED Requirements

### Requirement: Slash commands route to the same engine
<!-- anchor: cli.slash -->

`issue_comment` events SHALL parse into commands — `/review` (with `full`
forcing a full re-review), `/improve`, `/ask <q>` replying in-thread from a
task-specific answer object, `/describe` upserting a structured description,
and `/diagram` upserting a compact Mermaid change diagram rendered locally
from structured graph data — all dispatched to the same engine and provider
stack.

#### Scenario: reviewer comments /review full
- **WHEN** the comment body is `/review full`
- **THEN** a full review runs, bypassing triage and incremental scoping

#### Scenario: reviewer asks a question
- **WHEN** the comment body is `/ask <question>` and the provider returns a valid answer object
- **THEN** only the validated answer text is posted in-thread

#### Scenario: ask provider returns the wrong schema
- **WHEN** the `/ask` provider returns object or array JSON that does not contain a valid answer
- **THEN** the raw JSON is not posted and the comment explains that no valid answer was produced

#### Scenario: reviewer comments /diagram
- **WHEN** the comment body is `/diagram`
- **THEN** typed nodes and edges are rendered into Mermaid and text views with
  stable ids, escaped labels, compact cards, and change markers on nodes

#### Scenario: provider returns legacy C4
- **WHEN** the diagram provider ignores the graph contract and returns C4
  source together with an ASCII rendering
- **THEN** the C4 source is not posted as Mermaid and the ASCII rendering is
  shown instead
