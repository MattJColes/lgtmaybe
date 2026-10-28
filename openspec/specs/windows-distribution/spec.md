# windows-distribution Specification

## Purpose

Windows compatibility coverage and the release chain that builds, verifies,
publishes, and distributes the portable CLI executable.
## Requirements
### Requirement: Supported Windows versions run the full CI gate

The main CI workflow SHALL run the same test, lint, format, and type-check gate
on Ubuntu and Windows using only the minimum supported Python version. The
Windows job MUST exercise locale-default encoding behavior rather than forcing
Python UTF-8 mode.
<!-- anchor: windows.ci -->

#### Scenario: a change breaks only under Windows path semantics
- **WHEN** the pull request test matrix runs
- **THEN** the Windows job fails before the shared required check can pass

#### Scenario: the routine CI gate expands its test matrix
- **WHEN** the main CI workflow builds its test jobs
- **THEN** it creates exactly one Ubuntu job and one Windows job on the minimum
  supported Python version

### Requirement: Releases include a smoke-tested portable executable

Each release SHALL build a Windows x86_64 portable `lgtmaybe.exe` with Python
3.13, bundle the package and litellm data needed at runtime plus ast-grep, and
attach the versioned artifact only after CLI smoke commands and a Defender
scan with updated signatures pass. The executable MUST NOT bundle optional
cloud authentication dependencies. The packaging environment MUST compile the
locked PyInstaller bootloader from source without reusing a cached wheel, and
the executable MUST disable optional UPX compression.
<!-- anchor: windows.executable -->

#### Scenario: a lazy import is absent from the bundle
- **WHEN** the built executable cannot run the required CLI smoke commands
- **THEN** the workflow fails without uploading the release asset

#### Scenario: Defender detects a threat or cannot complete the scan
- **WHEN** the release binary fails the Defender check
- **THEN** the workflow retains scan logs and the binary hash without publishing
  the executable

### Requirement: winget updates follow executable publication

After the executable asset is published, the release workflow SHALL submit an
update for `MattJColes.lgtmaybe` through winget using the versioned asset URL.
The reusable workflow MUST also support a manually dispatched recovery run
and scan the published bytes with updated Defender signatures before submission.
<!-- anchor: windows.winget -->

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

#### Scenario: an existing release needs a Defender recheck
- **WHEN** a maintainer dispatches the Windows Defender release check
- **THEN** it downloads and scans the exact release asset, retains evidence,
  and never rebuilds or replaces the executable
