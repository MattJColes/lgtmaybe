## Context

`actions/create-github-app-token@v3` accepts the legacy `app-id` key but emits
a deprecation warning and recommends `client-id`. lgtmaybe exposes its own
`app_id` input, which existing self-managed App users already configure.
GitHub accepts either a client ID or application ID as the JWT issuer.

## Goals / Non-Goals

**Goals:**

- Stop the upstream deprecation warning.
- Preserve existing lgtmaybe workflow configuration.
- Pin the supported nested input in a structural test.

**Non-Goals:**

- Rename or remove lgtmaybe's public `app_id` input.
- Change the public brokered identity path.
- Alter App permissions, token scope, or key handling.

## Decisions

Keep `app_id` as lgtmaybe's compatibility boundary and forward its value using
the upstream action's `client-id` key. This is the smallest safe change because
GitHub accepts both ID forms as a JWT issuer, while the warning is attached to
the deprecated input name.

Adding a second lgtmaybe input was rejected: it would add precedence,
validation, documentation, and migration work without changing token behavior.
Removing `app_id` was rejected as a breaking change.

## Risks / Trade-offs

- [The upstream action later requires the client-ID format] → Introduce a new
  lgtmaybe input only if that concrete compatibility break occurs.
- [The deprecated key returns during refactoring] → The structural test rejects
  `app-id` and requires `client-id`.

## Migration Plan

No user migration is required. Release the composite-action change; rollback is
the one-line nested-key reversal.

## Open Questions

None.
