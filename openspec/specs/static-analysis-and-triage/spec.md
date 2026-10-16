# static-analysis-and-triage Specification

## Purpose

The stages that run before the lens fan-out: sandboxed static-analysis
tools whose output becomes a hint or a posted finding by mode
(`engine/static_analysis.py`), the scan-only channel for lockfiles and
manifests, and two-stage triage (`engine/triage.py`) with its security
floor.

## Requirements

### Requirement: Triage never skips past the security floor

With `triage_model` set, a cheap model SHALL rank files and skip
plainly-non-substantive ones — but a deterministic floor (security
paths/tokens, static-analysis hits, large hunks) always escalates past triage,
any triage failure reviews everything, and skips are named in the summary.
<!-- anchor: engine.triage -->

#### Scenario: triage tries to skip a security-sensitive file
- **WHEN** a file the cheap model would skip matches the security floor
- **THEN** the strong model reviews it anyway

### Requirement: Static analysis runs sandboxed

Installed tools SHALL run sandboxed when static analysis is enabled, which is
the default: scrubbed
env, no network, hard timeout, throwaway temp dir, never a checkout, and rules
read only from a local path — bundled with the package or configured — never a
network rule registry. Paths MUST be canonical forward-slash repository
paths. On Windows the scrubbed environment MUST pass through process-critical
system variables while pinning user config and profile directories to the temp
root. A tool that is not installed is skipped, and any tool failure degrades to
no output from that tool rather than failing the review. No tool MAY take its
config, ignore files or inline ignore pragmas from the PR-authored corpus.
<!-- anchor: engine.static-analysis -->

#### Scenario: a PR adds a scanner config that hides its own bug
- **WHEN** a PR adds `sgconfig.yml`, `.ignore`, `.bandit`, `.semgrepignore`,
  `zizmor.yml`, `.gitleaks.toml` or `osv-scanner.toml` beside a flagged line
- **THEN** the tool still reports that line, including under `.github/`

#### Scenario: a rules-driven tool has no rules
- **WHEN** static analysis runs a rules-driven tool for which no rules exist,
  neither bundled nor configured
- **THEN** that tool does not run at all (never a network rule registry)

#### Scenario: a Windows tool reports a backslash path
- **WHEN** static analysis reports `.\src\app.py`
- **THEN** the finding is associated with the canonical diff path `src/app.py`

#### Scenario: static analysis runs on Windows
- **WHEN** a child analyzer starts under Windows
- **THEN** it receives the minimal process-critical system variables and temp-
  rooted user directories without inheriting cloud credentials

### Requirement: Scan-only file texts never reach a prompt

Dependency manifests and lockfiles fetched for scanning SHALL travel in their
own context channel, separate from reviewed file texts. Their text MUST NOT
enter the diff, any prompt, a hint block, or the reflection pass (only a
scanner's own redacted hint about them may), and they are fetched only when a
scanner will read them.
<!-- anchor: engine.scan-manifests -->

#### Scenario: a PR changes a lockfile
- **WHEN** a reviewed PR changes a lockfile and static analysis is enabled
- **THEN** the scanner reads it and no model call contains any of its content

#### Scenario: static analysis is off
- **WHEN** static analysis is disabled
- **THEN** no dependency manifest is fetched at all

### Requirement: Tool findings ground or post, by mode

Each tool's findings SHALL reach the review by its configured mode: `hint`
wraps them as untrusted grounding for the lens calls, while `finding` maps them
onto review findings posted with no model call. Every tool MUST default to
`hint`; `finding` is a per-tool opt-in. Direct-posted findings MUST be
redacted, MUST carry a `scan:<tool>` category, and MUST be re-anchored, deduped,
suppressed and rule-filtered like any other finding — but never reflected, since
there is no model judgement to audit. They re-anchor against the diff the scan
corpus saw (triage or the file cap may since narrow the reviewed diff), are
capped most-severe-first only after diff scoping, and a finding whose anchor
matches no changed line MUST be dropped and counted in the summary, because
the tools read whole files and only the diff is under review.
<!-- anchor: engine.tool-findings -->

#### Scenario: a scanner runs with the default mode
- **WHEN** gitleaks reports a credential and no `tool_mode` is configured
- **THEN** the hit reaches the lens calls as a hint and nothing posts without
  the model

#### Scenario: a hint is about a file no batch holds
- **WHEN** a `hint`-mode tool reports an advisory against a lockfile
- **THEN** it rides the first batch's security lens (else correctness, else the
  summary names the drop) once, on that lens's own block, and a confirmed
  finding renders in the review body

#### Scenario: a scanner hits a line the PR changed
- **WHEN** a `finding`-mode tool reports a credential on a changed line
- **THEN** the review posts it without any model call, and no raw secret value
  appears in the finding

#### Scenario: a scanner hits pre-existing code
- **WHEN** a `finding`-mode tool reports an issue on an unchanged line
- **THEN** the finding is dropped and the summary states how many were skipped

#### Scenario: a dependency advisory has no line to anchor to
- **WHEN** a vulnerability scanner reports an advisory against a lockfile the
  review never anchors against
- **THEN** the finding is exempt from that drop and renders in the review body
