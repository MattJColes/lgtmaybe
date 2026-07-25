## Context

The dogfood workflow listens for PR events and comment commands. It currently
declares concurrency at workflow scope, so every matching event acquires the
per-PR group before the `review` job's author-association guard is evaluated.
An App-authored diagram comment therefore starts a skipped run that cancels the
active review.

## Goals / Non-Goals

**Goals:**

- Prevent ineligible bot or untrusted comment events from canceling reviews.
- Keep newest-run-wins behavior between eligible jobs for one PR.
- Retain the existing triggers, eligibility guard, App identity, and automatic
  diagram behavior.

**Non-Goals:**

- Suppressing GitHub App events.
- Changing review commands, authentication, or diagram posting.
- Adding a second workflow or scheduler.

## Decisions

Move the existing concurrency mapping unchanged beneath `jobs.review`. GitHub
then evaluates the job guard before an eligible job can occupy the group.
Eligible PR events and trusted commands still share the same per-PR key and
retain `cancel-in-progress: true`.

A conditional workflow-level `cancel-in-progress` was rejected because an
ineligible run would still occupy the group and could serialize or replace
eligible work. Disabling auto-diagrams was rejected because the diagram merely
exposed the workflow-scoping bug.

The regression test parses the workflow and pins concurrency to the guarded
review job. GitHub's scheduler cannot be simulated locally, so the next
post-merge dogfood run remains the runtime verification.

## Risks / Trade-offs

- [GitHub changes job-concurrency scheduling semantics] → Keep the regression
  focused on the documented job-level structure and verify the dogfood run
  after merge.
- [A future unguarded job is added] → Its author must choose its own concurrency
  behavior explicitly; it will not silently inherit review cancellation.

## Migration Plan

Merge the workflow change. Existing runs are unaffected; new runs use job-level
concurrency immediately. Roll back by restoring the top-level mapping.

## Open Questions

None.
