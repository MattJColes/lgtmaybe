## Why

Comments posted with lgtmaybe's GitHub App token emit new `issue_comment`
workflow runs. Those skipped bot runs currently enter the workflow-level
concurrency group before the review job's eligibility guard runs, canceling the
active review that posted the comment.

## What Changes

- Scope per-PR concurrency to the eligible review job.
- Preserve newest-run-wins cancellation between eligible reviews and trusted
  slash commands for the same PR.
- Add a structural regression test for the workflow placement.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-posting`: Ineligible workflow events must not preempt an active,
  eligible review for the same PR.

## Impact

The dogfood GitHub Actions workflow and its structural tests change. There are
no public API, configuration, authentication, or dependency changes.
