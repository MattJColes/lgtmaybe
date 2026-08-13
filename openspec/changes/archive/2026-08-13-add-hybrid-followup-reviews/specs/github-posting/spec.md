## MODIFIED Requirements

### Requirement: Incremental review is commit-scoped and safe

A follow-up run with a completed-head watermark SHALL review only the compare-API diff since that head and explicitly validate earlier open findings against the new head. The completed watermark SHALL move only after a non-partial review result and, when automatic diagrams are enabled, the current-head diagram have posted; force-push, missing marker, compare failure, and incomplete attempts SHALL fall back to a full review. A run whose head already equals the completed head SHALL make no model calls or duplicate posts. LEFT-side new findings SHALL be dropped and resolution scope SHALL remain limited to findings actually validated.
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

### Requirement: Resolving threads is best-effort GraphQL

A follow-up run SHALL classify each earlier active finding as `fixed`, `still_open`, or `uncertain` from structured model output. Only `fixed` findings SHALL be resolved via GraphQL; `still_open`, `uncertain`, malformed, and missing verdicts SHALL remain open without repetitive replies. After a successful resolve, the opening comment's fingerprint and identity markers SHALL be rewritten into disjoint resolved families before the one cosmetic fixed reply is attempted. Each thread remains independently best-effort and identity-wide permission refusal SHALL stop further resolution attempts in that wave without failing the review.
<!-- anchor: github.resolve-fixed -->

#### Scenario: a prior finding is explicitly fixed
- **WHEN** validation returns a well-formed `fixed` verdict for its active identity
- **THEN** its thread is resolved, its active markers are rewritten, and one fixed reply is attempted

#### Scenario: validation cannot prove a fix
- **WHEN** validation returns `still_open`, `uncertain`, malformed output, or no verdict
- **THEN** the thread remains open and receives no repetitive reply

#### Scenario: GraphQL call errors
- **WHEN** `resolveReviewThread` fails
- **THEN** the review still completes and posts normally

#### Scenario: one thread of several fails
- **WHEN** several findings are fixed and one errors mid-resolve
- **THEN** the remaining threads are still independently attempted

#### Scenario: the identity may not resolve threads
- **WHEN** `resolveReviewThread` is refused for the configured identity
- **THEN** no reply is posted and no marker is rewritten, remaining work stays retryable, and the review still completes

#### Scenario: the reply fails after a successful resolve
- **WHEN** the thread resolves but its reply errors
- **THEN** the active markers are still rewritten so the resolved finding may reappear later if the problem returns
