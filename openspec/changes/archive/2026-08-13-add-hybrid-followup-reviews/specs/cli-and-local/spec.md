## MODIFIED Requirements

### Requirement: One orchestrator behind every entrypoint

`run_review` SHALL orchestrate the shared flow — completed-head read, same-head no-op, incremental vs full decision, explicit earlier-finding validation, engine call, posting, and completion stamping — so `review`, `comment`, and `action` never duplicate review logic. Automatic synchronize runs SHALL use the hybrid incremental path; explicit `incremental: false` and `/review full` SHALL run a full review.
<!-- anchor: cli.run-review -->

#### Scenario: Action synchronize event
- **WHEN** the Action runs on `synchronize` with `incremental` unset after a completed review
- **THEN** the same orchestrator scans the new compare diff and validates earlier open findings

#### Scenario: reviewer forces a full review
- **WHEN** `/review full` runs after a completed review
- **THEN** completion state is ignored and the entire PR is reviewed again

### Requirement: Starter workflows enable automatic diagrams

The supplied GitHub Actions starter workflows SHALL opt in to automatic change diagrams and the dogfood workflow SHALL keep the same setting while using the faster default review preset. When enabled, automatic diagrams SHALL refresh on `opened`, `reopened`, and `synchronize` events from the full current PR context, post after the review result, and carry the head marker that proves the end-to-end run completed. When explicitly disabled, the posted review result alone SHALL be the completion watermark.
<!-- anchor: cli.starter-workflow-diagrams -->

#### Scenario: New repository adopts a supplied workflow
- **WHEN** a maintainer copies a supplied provider workflow into a repository
- **THEN** the workflow passes `auto_diagram: true` to the lgtmaybe Action

#### Scenario: Faster default is adopted
- **WHEN** the supplied workflow runs a default review
- **THEN** automatic change-diagram generation remains enabled

#### Scenario: A new head completes
- **WHEN** a non-partial review and required diagram both post for the current head
- **THEN** later synchronize runs may use that head as their hybrid-review base

#### Scenario: Diagram generation fails
- **WHEN** automatic diagrams are enabled and the current-head diagram cannot be generated or posted
- **THEN** the run fails without advancing completion, even if its review result already posted
