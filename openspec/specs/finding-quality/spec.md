# finding-quality Specification

## Purpose

The passes between raw lens output and what gets posted: suppression,
self-reflection with cross-file humility (`engine/reflect.py`), confidence
scoring, cross-file symbol resolution for deferred verdicts, and declarative
finding rules — all biased to never silently drop a real finding.

## Requirements

### Requirement: Self-reflection with a keep-all safe default

After merge/dedupe the provider SHALL audit its own findings and drop the ones
it marks low-confidence, including cross-file false positives whose validity
hinges on unshown code — while gap findings (a missing test/doc on the diff
itself) stay valid. An unparseable audit keeps everything.
<!-- anchor: quality.reflect -->

#### Scenario: reflection output can't be parsed
- **WHEN** the audit reply fails to parse
- **THEN** every finding is kept — a broken audit never silently drops findings

### Requirement: Verdicts are lenient to read, strict to act on

Each kept verdict SHALL carry a 0-10 confidence score (the auditor tries to
disprove the finding to reach it); `min_confidence` drops findings scored
below it, and an unscored kept finding survives any threshold.
<!-- anchor: quality.verdicts -->

#### Scenario: kept finding has no score
- **WHEN** a verdict keeps a finding but omits `confidence`
- **THEN** the finding survives even a `min_confidence` of 10

### Requirement: Suppressions apply before reflection

Known-fine findings SHALL be dropped before the reflection pass — ignored
fingerprints and inline suppression comments — so audit budget is never spent
on them.
<!-- anchor: quality.suppress -->

#### Scenario: a finding was previously dismissed
- **WHEN** its fingerprint is in `ignore_fingerprints`
- **THEN** it never reaches reflection or posting

### Requirement: Finding rules are declarative, never code

`finding_rules` SHALL be an ordered declarative match (path glob / lens
category / title / severity floor, ANDed) to action (`drop` / `set_severity`),
applied just before posting. Deliberately not a hook: no user code ever runs.
<!-- anchor: quality.rules -->

#### Scenario: a team downgrades a lens on a path
- **WHEN** a rule matches `category: complexity` under `legacy/**`
- **THEN** the action applies with no user code executed

### Requirement: Deferred verdicts resolve real definitions

A deferred cross-file symbol SHALL be located structurally with ast-grep in a
read-only corpus of the PR's base branch (never executed), so the auditor
re-judges against the real definition instead of guessing. Unsupported
languages or any ast-grep failure fall back to the plain verdict.
<!-- anchor: quality.symbol-resolution -->

#### Scenario: auditor names a symbol outside the diff
- **WHEN** a verdict needs `validate_tenant` defined in an unshown file
- **THEN** ast-grep finds its file in the corpus and the auditor re-judges
  against the actual code
