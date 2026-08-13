## MODIFIED Requirements

### Requirement: Starter workflows enable automatic diagrams
<!-- anchor: cli.starter-workflow-diagrams -->

The supplied GitHub Actions starter workflows SHALL opt in to automatic C4
change diagrams and the dogfood workflow SHALL keep the same setting while
using the faster default review preset. When automatic diagrams are enabled,
the Action SHALL post or update the diagram on `opened`, `reopened`, and
`synchronize` pull-request events.

#### Scenario: New repository adopts a supplied workflow
- **WHEN** a maintainer copies a supplied provider workflow into a repository
- **THEN** the workflow passes `auto_diagram: true` to the lgtmaybe Action

#### Scenario: Faster default is adopted
- **WHEN** the supplied workflow runs a default review
- **THEN** automatic C4 diagram generation remains enabled

#### Scenario: New push replaces an opened review
- **WHEN** a `synchronize` event replaces or follows the pull request's `opened`
  review while automatic diagrams are enabled
- **THEN** the surviving run posts or updates the change diagram

### Requirement: Change diagrams show structure and sequence
<!-- anchor: cli.diagram-sequence -->

The change diagram SHALL summarize what the pull request changes and render two
complementary views from the same structured call: a Mermaid flowchart of the
components the change touches, and a Mermaid sequence diagram of the ordered
run-time interactions it alters. The concise summary SHALL appear above the
diagrams in the same comment, lead with the highest-impact change, keep one
change per sentence, and omit preamble, process recap, filler, and tangents.
Steps referencing unknown components SHALL be dropped, the
step count SHALL be bounded, participant and message text SHALL be escaped with
Mermaid entity codes, and the sequence view SHALL be omitted — section headings
included — when the model reports no run-time flow.

#### Scenario: change alters a run-time flow
- **WHEN** the provider returns a change summary and ordered steps between known
  components
- **THEN** the summary renders above a `sequenceDiagram` beside the flowchart
  under `Structure` and `Sequence` headings, each view with its own text version
  and link

#### Scenario: change has no run-time flow
- **WHEN** the provider returns a change summary and no steps
- **THEN** the comment carries the summary and flowchart, with no sequence
  section and no diagram headings

#### Scenario: the same diagram printed in a terminal
- **WHEN** `lgtmaybe diagram` prints the body locally
- **THEN** the summary remains above the diagrams, each collapsible text version
  becomes a labelled section with its HTML wrapper removed, and the Mermaid
  source stays intact to paste elsewhere
