## MODIFIED Requirements

### Requirement: A project is addressed by its encoded path
<!-- anchor: gitlab.project -->

The adapter SHALL address a project by its URL-encoded full path, so an
arbitrarily nested group path survives as a single URL segment, and SHALL
preserve the complete merge-request server origin, including a nonstandard
port, rather than assuming gitlab.com.

#### Scenario: a nested group path is encoded
- **WHEN** the project is "group/subgroup/project"
- **THEN** requests address it as "group%2Fsubgroup%2Fproject"

#### Scenario: self-hosted GitLab uses a nonstandard port
- **WHEN** `CI_SERVER_URL` includes port 8443
- **THEN** the merge-request and API URLs retain port 8443
