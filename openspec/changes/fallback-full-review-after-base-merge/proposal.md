## Why

Incremental review compares the last completed PR head with the current head.
When a contributor merges the updated base branch into the PR, GitHub reports
that comparison as `ahead` and includes the base branch's commits and files.
lgtmaybe then reviews unrelated base-branch changes and can stamp the current
PR head complete without reviewing its current PR diff.

## What Changes

- Treat an incremental comparison containing a merge commit as unsafe.
- Fall back to the existing full-PR review path for that synchronization.
- Keep linear incremental pushes and the existing force-push/API-failure
  fallbacks unchanged.
- Isolate tests from active spec proposals in the developer checkout.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-posting`: Make incremental review fall back to a full review after a
  branch merge.

## Impact

- Incremental comparison handling in `src/lgtmaybe/github/rest_gateway.py`.
- Focused HTTP-boundary coverage in `tests/github/test_incremental.py`.
- Shared test working-directory isolation and repository-anchored docs tests.
- One `github-posting` specification scenario; no API, configuration,
  dependency, or marker changes.
