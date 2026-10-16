## Purpose

Turns a CI run's own OIDC ID token into keyless Bedrock, Azure and Vertex
credentials inside the lgtmaybe container, so a runner without the GitHub
composite action (Forgejo Actions) still needs no static cloud key.

## ADDED Requirements

### Requirement: Federation runs only when an input asks for it

The `action` entrypoint SHALL federate a cloud only when that cloud's input is
set: `INPUT_AWS_ROLE_ARN` for Bedrock, `INPUT_AZURE_CLIENT_ID` for Azure,
`INPUT_GCP_WIF_PROVIDER` for Vertex. With none set it SHALL make no request and
change no environment variable, so every existing run is byte-identical.
<!-- anchor: federation.gate -->

#### Scenario: no federation input
- **WHEN** a run sets none of the three inputs
- **THEN** no token is requested and the environment is unchanged

#### Scenario: the GitHub composite action
- **WHEN** the composite action has already exchanged the token in its own
  steps
- **THEN** the container receives none of the three inputs and federation does
  not run a second time

### Requirement: A missing ID token fails with the fix

The run SHALL fail before any model or forge call when a federation input is
set but `ACTIONS_ID_TOKEN_REQUEST_URL` or `ACTIONS_ID_TOKEN_REQUEST_TOKEN` is
absent, naming the workflow setting that grants the token
(`enable-openid-connect: true` on Forgejo, `id-token: write` on GitHub). It
SHALL NOT fall back to another credential.
<!-- anchor: federation.missing-token -->

#### Scenario: OpenID Connect left off
- **WHEN** `INPUT_AWS_ROLE_ARN` is set and the job did not enable OpenID Connect
- **THEN** the run exits non-zero naming `enable-openid-connect: true`

### Requirement: An incomplete input set fails before the token request

Each cloud's federation inputs SHALL be checked as a set before any token is
requested: Azure needs `INPUT_AZURE_CLIENT_ID` and `INPUT_AZURE_TENANT_ID`, and
Bedrock needs `INPUT_AWS_ROLE_ARN` and a region (`INPUT_AWS_REGION`, else
`AWS_REGION` or `AWS_DEFAULT_REGION`). A missing companion SHALL fail the run naming that input, rather
than surfacing later as an SDK error.
<!-- anchor: federation.inputs -->

#### Scenario: Azure without a tenant
- **WHEN** `INPUT_AZURE_CLIENT_ID` is set and `INPUT_AZURE_TENANT_ID` is not
- **THEN** the run exits non-zero naming `INPUT_AZURE_TENANT_ID`, and no token
  is requested

### Requirement: Each cloud gets the credential its SDK already reads

Federation SHALL request the ID token with the audience the cloud expects and
SHALL expose it only through the variables that cloud's SDK reads natively, so
credential resolution itself is unchanged.
<!-- anchor: federation.exchange -->

#### Scenario: Bedrock
- **WHEN** `INPUT_AWS_ROLE_ARN` is set
- **THEN** the token is requested for audience `sts.amazonaws.com` and
  `AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE` and, from `INPUT_AWS_REGION`,
  `AWS_REGION` are set

#### Scenario: Azure
- **WHEN** `INPUT_AZURE_CLIENT_ID` and `INPUT_AZURE_TENANT_ID` are set
- **THEN** the token is requested for audience `api://AzureADTokenExchange` and
  `AZURE_FEDERATED_TOKEN_FILE`, `AZURE_CLIENT_ID` and `AZURE_TENANT_ID` are set

#### Scenario: Vertex
- **WHEN** `INPUT_GCP_WIF_PROVIDER` is set
- **THEN** the token is requested for audience
  `https://iam.googleapis.com/<provider>` and `GOOGLE_APPLICATION_CREDENTIALS`
  points at an `external_account` configuration reading it, impersonating
  `INPUT_GCP_SERVICE_ACCOUNT` when that is set

### Requirement: The ID token never leaks

The ID token and the request token SHALL never be logged, posted or sent to a
model. The token file SHALL be readable by its owner only and live in a fresh
temporary directory, removed when the process exits. A failed token request SHALL fail the run, and its message
SHALL carry the HTTP status but never a token or the request URL's query string.
<!-- anchor: federation.secrecy -->

#### Scenario: the token request is refused
- **WHEN** the runner's token endpoint returns 403
- **THEN** the run exits non-zero with the status and no token text in its output

#### Scenario: the token file
- **WHEN** a token is written
- **THEN** the file's mode is `0600` on POSIX
