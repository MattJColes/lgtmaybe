## ADDED Requirements

### Requirement: Ineligible events cannot preempt active reviews
<!-- anchor: github.workflow-concurrency -->

The dogfood workflow SHALL apply per-PR concurrency only to an event that passes
the review job's eligibility guard. Eligible jobs for the same PR MUST retain
newest-run-wins cancellation.

#### Scenario: lgtmaybe posts an automatic diagram

- **WHEN** the GitHub App comment emits an `issue_comment` workflow run whose
  author does not pass the review job guard
- **THEN** that run does not enter the review concurrency group or cancel the
  active review

#### Scenario: a newer eligible review starts

- **WHEN** a newer PR event or trusted command passes the review job guard for
  the same PR
- **THEN** the newer job cancels the older eligible review job
