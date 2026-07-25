## ADDED Requirements

### Requirement: Starter workflows enable automatic diagrams
<!-- anchor: cli.starter-workflow-diagrams -->

The supplied GitHub Actions starter workflows SHALL opt in to automatic change
diagrams so a newly opened or reopened pull request receives a diagram without
additional repository configuration.

#### Scenario: New repository adopts a supplied workflow
- **WHEN** a maintainer copies a supplied provider workflow into a repository
- **THEN** the workflow passes `auto_diagram: true` to the lgtmaybe Action
