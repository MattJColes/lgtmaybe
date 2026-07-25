## ADDED Requirements

### Requirement: App-authenticated PR activity carries the lgtmaybe identity
<!-- anchor: github.app-attribution -->

The GitHub adapter SHALL use the selected credential uniformly for review
comments, summary comments, slash-command replies, thread resolution, labels,
descriptions, and diagrams so opted-in activity is attributable to the
installed lgtmaybe App. Public branded mode SHALL reject `fail_on` because the
least-privilege public App does not hold `checks: write`.

#### Scenario: Branded review completes
- **WHEN** the engine posts a review using a brokered lgtmaybe App installation token
- **THEN** GitHub attributes every supported write in that run to `lgtmaybe[bot]`

#### Scenario: Public branded review requests a merge gate
- **WHEN** `github_identity` is `lgtmaybe` and `fail_on` is set
- **THEN** the Action fails before token exchange with guidance to use Actions identity or a self-managed App

#### Scenario: Default review completes
- **WHEN** the engine posts a review using the built-in workflow token
- **THEN** existing `github-actions[bot]` posting behavior remains unchanged
