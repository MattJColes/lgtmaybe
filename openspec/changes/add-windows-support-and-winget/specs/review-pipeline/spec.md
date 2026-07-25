## MODIFIED Requirements

### Requirement: Path filters apply after the skip filter
<!-- anchor: engine.path-filters -->

The user's `include_paths` allowlist and `exclude_paths` denylist SHALL apply
right after generated/binary skipping; exclude wins, `**/`-prefixed patterns
also match at the repo root, and matching repository paths is case-sensitive
on every host.

#### Scenario: a file is both included and excluded
- **WHEN** a path matches `include_paths` and `exclude_paths`
- **THEN** it is excluded

#### Scenario: path case differs from the configured glob
- **WHEN** a repository path differs from a configured include or exclude glob
  only by letter case
- **THEN** the path does not match on Windows or POSIX hosts

### Requirement: Static analysis grounds, never posts
<!-- anchor: engine.static-analysis -->

Installed tools SHALL run sandboxed when static analysis is enabled - ruff,
bandit, and semgrep with local rules only; scrubbed env, no network, hard
timeout, temp dir, never a checkout - and their findings enter the prompt only
as an untrusted HINTS block ("confirm, contextualise, or discard"). Paths MUST
be canonical forward-slash repository paths. On Windows the scrubbed
environment MUST pass through process-critical system variables while pinning
user config and profile directories to the temp root. Raw tool findings are
never posted; a missing tool is skipped silently.

#### Scenario: semgrep has no local rules
- **WHEN** static analysis runs without `semgrep_rules` configured
- **THEN** semgrep does not run at all (never `--config auto`)

#### Scenario: a Windows tool reports a backslash path
- **WHEN** static analysis reports `.\src\app.py`
- **THEN** the hint is associated with the canonical diff path `src/app.py`

#### Scenario: static analysis runs on Windows
- **WHEN** a child analyzer starts under Windows
- **THEN** it receives the minimal process-critical system variables and temp-
  rooted user directories without inheriting cloud credentials
