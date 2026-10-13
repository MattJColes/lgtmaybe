## MODIFIED Requirements

### Requirement: One orchestrator behind every entrypoint

`run_review` SHALL orchestrate the shared flow — completed-head read, whole-PR risk assessment, same-head no-op, incremental vs full decision, explicit earlier-finding validation, engine call, posting, and completion stamping — so `review`, `comment`, `action`, and `gitlab-ci` never duplicate review logic. Automatic synchronize runs SHALL use the hybrid incremental path, and so SHALL every `gitlab-ci` pipeline with `incremental` unset (GitLab CI has no synchronize action; the missing watermark makes the first pipeline full); explicit `incremental: false`, `gitlab-ci --no-incremental` / `LGTMAYBE_INCREMENTAL=false`, and `/review full` SHALL run a full review.
<!-- anchor: cli.run-review -->

#### Scenario: Action synchronize event
- **WHEN** the Action runs on `synchronize` with `incremental` unset after a completed review
- **THEN** the same orchestrator scans the new compare diff and validates earlier open findings

#### Scenario: reviewer forces a full review
- **WHEN** `/review full` runs after a completed review
- **THEN** completion state is ignored and the entire PR is reviewed again

#### Scenario: a later GitLab CI pipeline
- **WHEN** `gitlab-ci` runs with `incremental` unset on a merge request with a completed watermark
- **THEN** only the commits since the watermark are reviewed
