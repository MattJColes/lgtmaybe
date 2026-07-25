## ADDED Requirements

### Requirement: Installing the App opts a repository into branded identity
<!-- anchor: github-app.install -->

The public lgtmaybe GitHub App SHALL act only as an identity and
least-privilege repository-access layer; review execution and provider
credentials SHALL remain in the repository's GitHub Actions workflow.

#### Scenario: User chooses branded posting
- **WHEN** a repository administrator installs the lgtmaybe App on a repository and selects the lgtmaybe identity in its workflow
- **THEN** review activity is eligible to post as `lgtmaybe[bot]` without an App private key in that repository

#### Scenario: User keeps the zero-install path
- **WHEN** a workflow keeps the default Actions identity
- **THEN** lgtmaybe uses the built-in workflow token and does not contact the identity broker

### Requirement: The broker trusts only the requesting installed repository
<!-- anchor: github-app.exchange -->

The token broker MUST verify the GitHub OIDC signature, issuer, audience,
time, repository identity, default-branch workflow reference, and allowed
event before checking that the lgtmaybe App is installed on that repository.

#### Scenario: Valid workflow exchanges identity
- **WHEN** an allowed base-safe workflow presents a valid OIDC token for a repository where the App is installed
- **THEN** the broker returns a short-lived installation token limited to that exact repository

#### Scenario: Untrusted identity is rejected
- **WHEN** any required claim is invalid, the event is not allowed, or the App is not installed on the claimed repository
- **THEN** the broker returns no GitHub credential and a non-secret diagnostic

### Requirement: Installation tokens carry minimum permissions
<!-- anchor: github-app.scope -->

The App registration and every brokered token SHALL grant only repository
contents read, pull requests write, issues write, and required metadata access.

#### Scenario: Installation covers multiple repositories
- **WHEN** the broker mints a token for one repository in a multi-repository installation
- **THEN** the token is explicitly restricted to the verified repository ID and cannot access sibling repositories

### Requirement: The broker is an identity-only data boundary
<!-- anchor: github-app.boundary -->

The broker MUST NOT receive or persist provider credentials, diffs, file
contents, prompts, findings, or repository configuration, and MUST NOT log raw
OIDC or installation tokens.

#### Scenario: Action exchanges identity
- **WHEN** the Action calls the broker
- **THEN** it sends only the GitHub OIDC credential and protocol metadata needed to validate and complete the exchange

### Requirement: Branded identity fails loud
<!-- anchor: github-app.failure -->

The Action SHALL NOT silently fall back to `github-actions[bot]` after a user
explicitly selects the lgtmaybe identity.

#### Scenario: Broker is unavailable
- **WHEN** branded identity is selected and the bounded exchange cannot complete
- **THEN** the Action fails with setup or service guidance before posting review output
