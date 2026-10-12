## MODIFIED Requirements

### Requirement: Posting is idempotent via a hidden marker

Reviews SHALL reuse the latest matching provider/model summary across all pages
when its native commit equals the reviewed commit. A completed different-commit
review SHALL create a fresh summary at its actual reviewed commit, preserving
prior summaries and one-for-one inline dedupe. The hidden marker carries the
last-reviewed-SHA watermark that drives incremental review.
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

