## Why

GitLab CI always provides `CI_SERVER_HOST`, so the entrypoint ignores the full
`CI_SERVER_URL` and drops a self-hosted instance's nonstandard port.

## What Changes

- Prefer GitLab's complete server URL.
- Keep the host-derived HTTPS fallback for sparse test or compatible CI environments.

## Capabilities

### Modified Capabilities

- `gitlab-posting`: Preserve the configured self-hosted GitLab origin.

## Impact

- GitLab CI URL construction and one entrypoint test.
