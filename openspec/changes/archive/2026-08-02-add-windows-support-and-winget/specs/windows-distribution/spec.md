## ADDED Requirements

### Requirement: Supported Windows versions run the full CI gate
<!-- anchor: windows.ci -->

The main CI workflow SHALL run the same test, lint, format, and type-check gate
on Windows with Python 3.11 and 3.13 while retaining Ubuntu coverage for every
supported Python version. The Windows jobs MUST exercise locale-default
encoding behavior rather than forcing Python UTF-8 mode.

#### Scenario: a change breaks only under Windows path semantics
- **WHEN** the pull request test matrix runs
- **THEN** a Windows job fails before the shared required check can pass

### Requirement: Releases include a smoke-tested portable executable
<!-- anchor: windows.executable -->

Each release SHALL build a Windows x86_64 portable `lgtmaybe.exe` with Python
3.13, bundle the package and litellm data needed at runtime plus ast-grep, and
attach the versioned artifact only after CLI smoke commands pass. The
executable MUST NOT bundle optional cloud authentication dependencies.

#### Scenario: a lazy import is absent from the bundle
- **WHEN** the built executable cannot run the required CLI smoke commands
- **THEN** the workflow fails without uploading the release asset

### Requirement: winget updates follow executable publication
<!-- anchor: windows.winget -->

After the executable asset is published, the release workflow SHALL submit an
update for `MattJColes.lgtmaybe` through winget using the versioned asset URL.
The reusable workflow MUST also support a manually dispatched recovery run.

#### Scenario: release automation publishes a new version
- **WHEN** release-please creates a release and the executable workflow succeeds
- **THEN** the winget workflow submits the new version, URL, and checksum to the
  existing portable package

#### Scenario: the initial package does not exist
- **WHEN** maintainers prepare the first winget release
- **THEN** they create the portable manifest manually before automated update
  submissions begin

#### Scenario: release automation runs before initial moderation completes
- **WHEN** the package is not yet present in `microsoft/winget-pkgs`
- **THEN** the winget workflow warns and skips the update without failing the
  other release artifacts
