## Why

GitLab deduplication reads hidden ids from resolved discussions. A finding that
was fixed, resolved, and later reintroduced is therefore suppressed forever.

## What Changes

- Deduplicate against unresolved discussions only.
- Allow a resolved finding to be reported again if it returns.

## Capabilities

### Modified Capabilities

- `gitlab-posting`: Retire resolved discussions from active deduplication.

## Impact

- GitLab discussion scanning and one regression test.
