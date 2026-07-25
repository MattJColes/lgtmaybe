# github-app-identity Specification

## Purpose

The optional public GitHub App identity and its hosted token broker: repositories
keep review execution and model credentials in GitHub Actions while GitHub
writes can be attributed to `lgtmaybe[bot]`.

## Requirements

### Requirement: Installing the App opts into branded identity

The public lgtmaybe GitHub App SHALL act only as an identity and
least-privilege repository-access layer; review execution and provider
credentials remain in the repository's workflow.
<!-- anchor: github-app.install -->

#### Scenario: User keeps the zero-install path
- **WHEN** a workflow keeps the default Actions identity
- **THEN** it uses the workflow token and does not contact the identity broker

#### Scenario: User chooses branded posting
- **WHEN** the App is installed and lgtmaybe identity is selected
- **THEN** review activity can post as `lgtmaybe[bot]` without a repository App key

### Requirement: Self-managed App minting avoids deprecated inputs

The Action SHALL keep its public `app_id` input and pass that value to
`actions/create-github-app-token` through the supported `client-id` key, never
the deprecated `app-id` key.
<!-- anchor: github-app.self-managed-mint -->

#### Scenario: Existing self-managed workflow runs

- **WHEN** a repository supplies its existing `app_id` and private key
- **THEN** the Action mints the App token without an `app-id` deprecation
  warning or a workflow configuration change

### Requirement: The broker trusts only the installed repository

The token broker MUST verify GitHub OIDC signature, issuer, audience, time,
immutable repository identity, repository name, default-branch workflow
reference, and an allowed event before accepting the installation.
<!-- anchor: github-app.exchange -->

#### Scenario: Valid workflow exchanges identity
- **WHEN** an allowed workflow identifies an installed repository
- **THEN** the broker returns a short-lived token limited to that repository

#### Scenario: Untrusted identity is rejected
- **WHEN** a required claim is invalid or the App is not installed
- **THEN** the broker returns no credential and a non-secret diagnostic

### Requirement: Installation tokens carry minimum permissions

Every brokered token SHALL grant only contents read, pull requests write,
issues write, and required metadata access on the verified repository.
<!-- anchor: github-app.scope -->

#### Scenario: Installation covers multiple repositories
- **WHEN** one repository in that installation requests a token
- **THEN** sibling repositories are excluded from the token

### Requirement: The broker is an identity-only boundary

The broker MUST NOT receive or persist provider credentials, diffs, file
contents, prompts, findings, or repository configuration, and MUST NOT log raw
OIDC or installation tokens.
<!-- anchor: github-app.boundary -->

#### Scenario: Action exchanges identity
- **WHEN** the Action calls the broker
- **THEN** it sends only its GitHub OIDC credential and protocol metadata

### Requirement: Branded identity fails loud

The Action SHALL NOT fall back to `github-actions[bot]` after a user explicitly
selects lgtmaybe identity.
<!-- anchor: github-app.failure -->

#### Scenario: Broker is unavailable
- **WHEN** the bounded exchange cannot complete
- **THEN** the Action fails with setup or service guidance before posting
