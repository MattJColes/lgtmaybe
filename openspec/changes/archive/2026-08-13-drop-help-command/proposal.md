## Why

The custom `lgtmaybe help` command manually reconstructs command contexts for behaviour Click already exposes through `--help`.

## What Changes

- Remove the custom help command and its alias-specific tests.
- Point examples, documentation, and smoke checks at native `--help` invocations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. Native Click help already remains available; this removes an undocumented implementation duplicate, so `skip_specs` is enabled.

## Impact

CLI registration, help tests, user docs, and the Windows smoke-command list change.
