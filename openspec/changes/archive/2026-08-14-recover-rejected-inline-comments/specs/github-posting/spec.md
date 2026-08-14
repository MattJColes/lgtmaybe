## MODIFIED Requirements

### Requirement: Posting is idempotent via a hidden marker
<!-- anchor: github.post-review -->

Reviews SHALL post as one batched REST review (inline comments + summary),
with a hidden marker comment enabling in-place updates on re-run — the marker
also carries the last-reviewed-SHA watermark that drives incremental review.
When GitHub rejects an individual rerun comment with 422, posting SHALL continue
and the rejected finding SHALL be preserved in the updated review body. Other
posting failures SHALL remain fatal.

#### Scenario: review re-runs on the same PR
- **WHEN** a review already exists from a prior run
- **THEN** the summary is updated in place, not duplicated

#### Scenario: an incomplete run re-runs on the same PR
- **WHEN** the summary carries the hidden incomplete marker and the body update
  is an in-place edit nobody is notified about
- **THEN** the notice also posts as a PR comment, so a partial review is never
  indistinguishable from a clean one

#### Scenario: GitHub rejects one rerun comment position
- **WHEN** one new inline comment returns 422 and later comments remain valid
- **THEN** later comments still post and the rejected finding appears in the
  review body instead of failing the whole review

#### Scenario: GitHub rejects a rerun comment for another reason
- **WHEN** an individual comment returns a non-422 error
- **THEN** the review fails without claiming that finding was delivered
