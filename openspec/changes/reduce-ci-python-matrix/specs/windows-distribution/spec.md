## MODIFIED Requirements

### Requirement: Supported Windows versions run the full CI gate
<!-- anchor: windows.ci -->

The main CI workflow SHALL run the same test, lint, format, and type-check gate
on Ubuntu and Windows using only the minimum supported Python version. The
Windows job MUST exercise locale-default encoding behavior rather than forcing
Python UTF-8 mode.

#### Scenario: a change breaks only under Windows path semantics
- **WHEN** the pull request test matrix runs
- **THEN** the Windows job fails before the shared required check can pass

#### Scenario: the routine CI gate expands its test matrix
- **WHEN** the main CI workflow builds its test jobs
- **THEN** it creates exactly one Ubuntu job and one Windows job on the minimum
  supported Python version
