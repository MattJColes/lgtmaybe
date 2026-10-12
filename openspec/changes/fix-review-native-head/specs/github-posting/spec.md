## MODIFIED Requirements

### Requirement: Posting is idempotent via a hidden marker

Reviews SHALL reuse the latest matching token-authenticated actor/provider/model
summary across all pages when its native commit equals the reviewed commit.
Unknown authenticated identity SHALL fail closed. A completed different-commit
review SHALL create a fresh summary at its reviewed commit, preserving history
and one-for-one inline dedupe; its hidden marker carries the incremental watermark.
When GitHub rejects a rerun position with 422, later comments SHALL post; rejected
findings SHALL remain in the body. Other posting failures SHALL remain fatal.
<!-- anchor: github.post-review -->

#### Scenario: review re-runs on the same PR
- **WHEN** the latest matching review is already bound to the reviewed commit
- **THEN** the summary is updated in place, not duplicated

#### Scenario: the reviewed commit advances
- **WHEN** the latest matching review has a different native commit
- **THEN** a fresh summary is posted at the actual reviewed commit after required
  inline writes succeed, while old summaries remain intact

#### Scenario: an incomplete run re-runs on the same PR
- **WHEN** the summary carries the hidden incomplete marker and the body update
  is an in-place edit nobody is notified about
- **THEN** the notice also posts as a PR comment, so a partial review is never
  indistinguishable from a clean one

#### Scenario: an incomplete re-run computed findings
- **WHEN** a re-run stamps no completion watermark but its calls produced new
  findings
- **THEN** findings post inline at the current head, or demote into the body if
  no head can be resolved; they are never silently dropped

#### Scenario: GitHub rejects one rerun comment position
- **WHEN** one new inline comment returns 422 and later comments remain valid
- **THEN** later comments post and the rejected finding appears in the body

#### Scenario: GitHub rejects a rerun comment for another reason
- **WHEN** an individual comment returns a non-422 error
- **THEN** the review fails without claiming that finding was delivered


### Requirement: Incremental review is commit-scoped and safe

A follow-up run with a completed-head watermark SHALL review only the compare-API diff since that head and explicitly validate earlier open findings against the new head. The completed watermark SHALL move only after a non-partial review result and, when automatic diagrams are enabled, the current-head diagram have posted; force-push, missing marker, compare failure, and incomplete attempts SHALL fall back to a full review. Completion summaries and diagrams SHALL belong to the authenticated token's GitHub viewer. A run whose head already equals the completed head SHALL make no model calls or duplicate posts. LEFT-side new findings SHALL be dropped and resolution scope SHALL remain limited to findings actually validated.
<!-- anchor: github.incremental -->

#### Scenario: a later push follows a complete review
- **WHEN** the current head is ahead of the completed head
- **THEN** only the compare diff is scanned for new problems and prior open findings are classified against the new head

#### Scenario: the completed head runs again
- **WHEN** a run starts on the same head recorded as complete
- **THEN** it succeeds without model calls or GitHub writes

#### Scenario: a review attempt is incomplete
- **WHEN** review calls fail, are interrupted, exceed a configured budget, or a required diagram fails
- **THEN** the completed watermark does not move, so the next run retries from the last complete head

#### Scenario: a review run fails
- **WHEN** posting fails after review
- **THEN** the watermark does not move, so nothing is skipped next run

