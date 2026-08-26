## Context

GitLab posts each finding separately, so one stale position can fail while the
rest of the review remains valid.

## Decision

Return whether each discussion post succeeded. Failed findings join the
existing demoted list before the summary note is rendered.

## Non-Goals

- Retry rejected positions.
- Change successful inline discussions.
