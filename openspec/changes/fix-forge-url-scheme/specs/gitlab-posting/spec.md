## MODIFIED Requirements

### Requirement: A project is addressed by its encoded path
<!-- anchor: gitlab.project -->

The adapter SHALL address a project by its URL-encoded full path, so an
arbitrarily nested group path survives as a single URL segment, and SHALL
preserve the HTTP or HTTPS scheme and host from the merge request URL rather
than assuming gitlab.com.

#### Scenario: a nested group path is encoded
- **WHEN** the project is "group/subgroup/project"
- **THEN** requests address it as "group%2Fsubgroup%2Fproject"

#### Scenario: self-hosted GitLab uses HTTP
- **WHEN** the merge-request URL starts with `http://`
- **THEN** GitLab API requests use `http://`
