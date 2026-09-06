## Context

`CI_SERVER_URL` already contains the scheme, hostname, and optional port. Rebuilding
it from `CI_SERVER_HOST` loses information.

## Decision

Use `CI_SERVER_URL` first and retain the existing host-based fallback.

## Non-Goals

- Change forge URL parsing.
- Add new GitLab CI variables.
