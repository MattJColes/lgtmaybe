## Why

Forge URL parsing discards the input scheme. Builders then use HTTPS by default,
so an HTTP self-hosted GitLab or Gitea instance is unreachable.

## What Changes

- Preserve the parsed HTTP or HTTPS scheme in the forge locator.
- Pass it to the GitLab and Gitea gateways.

## Capabilities

### Modified Capabilities

- `gitlab-posting`: Preserve the merge-request URL scheme.
- `gitea-posting`: Preserve the pull-request URL scheme.

## Impact

- Forge URL parsing, gateway construction, and one registry regression test.
