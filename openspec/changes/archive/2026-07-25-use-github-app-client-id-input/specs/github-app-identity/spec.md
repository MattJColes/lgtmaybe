## ADDED Requirements

### Requirement: Self-managed App minting avoids deprecated inputs
<!-- anchor: github-app.self-managed-mint -->

The Action SHALL keep its public `app_id` input and pass that value to
`actions/create-github-app-token` through the supported `client-id` key, never
the deprecated `app-id` key.

#### Scenario: Existing self-managed workflow runs

- **WHEN** a repository supplies its existing `app_id` and private key
- **THEN** the Action mints the App token without an `app-id` deprecation
  warning or a workflow configuration change
