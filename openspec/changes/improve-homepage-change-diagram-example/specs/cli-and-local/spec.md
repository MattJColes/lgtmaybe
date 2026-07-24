## ADDED Requirements

### Requirement: Homepage diagram conveys change breadth
<!-- anchor: cli.docs-homepage-diagram-breadth -->

The main documentation homepage SHALL use a representative C4-style Mermaid
example that distinguishes changed and new elements and maps a change across
multiple cooperating containers.

#### Scenario: Visitor evaluates the diagram feature
- **WHEN** a visitor views the homepage change-diagram example
- **THEN** they can see changed and new elements connected across application,
  asynchronous, data, and external-service boundaries

### Requirement: Homepage overview stays concise
<!-- anchor: cli.docs-homepage-overview -->

The main documentation homepage SHALL introduce every review category, its
trust boundaries, and its pull-request commands in no more than 400 source words
before the "Start here" section.

#### Scenario: Visitor scans the homepage
- **WHEN** a visitor reads from the hero to the "Start here" section
- **THEN** they reach the commands and diagram without losing any review category
