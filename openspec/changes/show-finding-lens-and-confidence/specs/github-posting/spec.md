## ADDED Requirements

### Requirement: A posted finding names its lens and confidence

Every posted finding SHALL carry the lens that raised it and the reflection
auditor's confidence in its title line, so a GitHub reader can weigh it without
leaving the PR. Both halves are omitted when absent — no category renders no
badge at all, and no score renders the lens alone — and the badge is visible
prose only, never part of the hidden fingerprint/identity markers that key
re-run dedupe and resolve-on-fix. Inline, demoted, and broad findings SHALL
render it identically.

#### Scenario: a scored finding from a lens
- **WHEN** a finding carries a category and a confidence score
- **THEN** its title line reads `**[HIGH · security · 8/10] Title**`

#### Scenario: reflection is off
- **WHEN** a finding has a category but no score
- **THEN** the confidence half is omitted rather than rendered empty

#### Scenario: a comment posted before badges existed
- **WHEN** a re-run reports a finding already posted with an unbadged title
- **THEN** it is still recognised and not posted again
