## MODIFIED Requirements

### Requirement: Starter workflows enable automatic diagrams
<!-- anchor: cli.starter-workflow-diagrams -->

The supplied GitHub Actions starter workflows SHALL opt in to automatic C4
change diagrams and the dogfood workflow SHALL keep the same setting while
using the faster default review preset.

#### Scenario: New repository adopts a supplied workflow
- **WHEN** a maintainer copies a supplied provider workflow into a repository
- **THEN** the workflow passes `auto_diagram: true` to the lgtmaybe Action

#### Scenario: Faster default is adopted
- **WHEN** the supplied workflow runs a default review
- **THEN** automatic C4 diagram generation remains enabled
