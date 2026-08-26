## MODIFIED Requirements

### Requirement: Incremental review is commit-scoped and safe
<!-- anchor: github.incremental -->

A follow-up run with a completed-head watermark SHALL review only a linear,
merge-free compare-API diff since that head and explicitly validate earlier
open findings against the new head. The completed watermark SHALL move only
after a non-partial review result and, when automatic diagrams are enabled,
the current-head diagram have posted. Force-push, missing marker, compare
failure, merge-containing comparison, and incomplete attempts SHALL fall back
to a full review. A run whose head already equals the completed head SHALL make
no model calls or duplicate posts. LEFT-side new findings SHALL be dropped and
resolution scope SHALL remain limited to findings actually validated.

#### Scenario: a later push follows a complete review
- **WHEN** the current head is ahead of the completed head without a merge
- **THEN** only the compare diff is scanned for new problems and prior open
  findings are classified against the new head

#### Scenario: the PR branch merges its updated base
- **WHEN** the comparison since the completed head contains a merge commit
- **THEN** the comparison is rejected and the current full PR diff is reviewed

#### Scenario: the completed head runs again
- **WHEN** a run starts on the same head recorded as complete
- **THEN** it succeeds without model calls or GitHub writes

#### Scenario: a review attempt is incomplete
- **WHEN** review calls fail, are interrupted, exceed a configured budget, or a
  required diagram fails
- **THEN** the completed watermark does not move, so the next run retries from
  the last complete head

#### Scenario: a review run fails
- **WHEN** posting fails after review
- **THEN** the watermark does not move, so nothing is skipped next run
