## ADDED Requirements

### Requirement: The Action selects GitHub identity explicitly
<!-- anchor: cli.github-identity -->

The GitHub Action SHALL expose a typed identity choice whose default uses the
built-in workflow token and whose lgtmaybe value performs the OIDC exchange
before the existing Action entrypoint runs.

#### Scenario: Identity input is omitted
- **WHEN** a user runs the Action without a GitHub identity input
- **THEN** the Action uses the supplied or default `github_token` exactly as before

#### Scenario: lgtmaybe identity is selected
- **WHEN** a user selects the lgtmaybe identity and grants `id-token: write`
- **THEN** the Action obtains a brokered installation token without requiring App credentials in workflow secrets

#### Scenario: Identity configuration conflicts
- **WHEN** a workflow selects the public lgtmaybe identity and also supplies self-managed App credentials
- **THEN** the Action fails before review execution with instructions to choose one identity path

### Requirement: Branded setup remains provider-independent
<!-- anchor: cli.github-identity-provider -->

Selecting a GitHub posting identity SHALL NOT change provider, model, provider
authentication, review configuration, or local CLI behavior.

#### Scenario: User changes only identity mode
- **WHEN** an existing Action workflow switches from Actions identity to lgtmaybe identity
- **THEN** its provider and review inputs continue to reach the same runtime entrypoint unchanged
