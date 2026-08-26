## MODIFIED Requirements

### Requirement: Context comes from the API, never a checkout
<!-- anchor: gitea.context -->

`get_pr_context` SHALL fetch the diff, metadata, and head text of reviewable
files through the Gitea REST API only — pull-request code is never checked out
or executed. Head file text is returned base64-encoded and SHALL be decoded
before it reaches the engine. The adapter SHALL preserve the HTTP or HTTPS
scheme and host from the pull request URL.

#### Scenario: metadata without SHAs is fatal
- **WHEN** the API returns a payload carrying no base or head SHA
- **THEN** the adapter raises rather than reviewing an empty diff

#### Scenario: self-hosted Gitea uses HTTP
- **WHEN** the pull-request URL starts with `http://`
- **THEN** Gitea API requests use `http://`
