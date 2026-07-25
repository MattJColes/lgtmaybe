## ADDED Requirements

### Requirement: Text boundaries are deterministic across host locales
<!-- anchor: cli.utf8-boundaries -->

The CLI, local git adapter, and configuration store SHALL read and write owned
text as UTF-8 on every host. External subprocess output MUST decode as UTF-8
with undecodable bytes replaced, and CLI stdout and stderr MUST emit safely
when the inherited stream uses a legacy Windows encoding.

#### Scenario: configuration contains non-Latin text
- **WHEN** a user stores and reloads non-Latin configuration values on Windows
- **THEN** the values round-trip as UTF-8 without locale-dependent corruption

#### Scenario: a clean review writes an emoji to redirected output
- **WHEN** stdout is redirected through a cp1252 text stream
- **THEN** the CLI emits the summary without raising `UnicodeEncodeError`

#### Scenario: git emits an undecodable byte
- **WHEN** the local git subprocess returns output that is not valid UTF-8
- **THEN** the command retains the decodable output and replaces only the
  malformed byte sequence
