## Why

The self-managed GitHub App path passes the legacy `app-id` input to
`actions/create-github-app-token@v3`, producing a deprecation warning before
and after every review. The existing lgtmaybe input remains valid, so this can
be fixed without a user migration.

## What Changes

- Forward lgtmaybe's existing `app_id` value through the upstream action's
  supported `client-id` input.
- Retain the public `app_id` contract for backward compatibility.
- Add a structural regression test that rejects the deprecated upstream key.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-app-identity`: Self-managed App token minting uses a supported
  upstream input without changing existing lgtmaybe workflow configuration.

## Impact

`action.yml`, its structural tests, and the GitHub App identity specification
change. No dependency, secret, or user configuration changes are required.
