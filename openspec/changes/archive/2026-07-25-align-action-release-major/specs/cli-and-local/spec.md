## ADDED Requirements

### Requirement: Action distribution major alignment
<!-- anchor: distribution.action-major -->

The GitHub Action SHALL default to the GHCR image whose floating major matches
the package major, and maintained workflow examples SHALL use that same Action
major.

#### Scenario: package major is released
- **WHEN** the package version belongs to major v1
- **THEN** the Action defaults to the v1 image and maintained workflows use
  `MattJColes/lgtmaybe@v1`

#### Scenario: a future major changes
- **WHEN** the package major changes without updating the Action image default
- **THEN** the deterministic distribution alignment test fails
