## Why

Adding a `ReviewConfig` option repeats its name through callback parameters, forwarding keywords, and an Action input allowlist, creating silent drift risk.

## What Changes

- Collect the local review callback's Click values as keyword arguments and forward configuration fields together.
- Derive Action-readable config names from `ReviewConfig.model_fields`, keeping only intentional exclusions explicit.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is wiring-only, so `skip_specs` is enabled.

## Impact

CLI and Action input plumbing changes without changing flags, defaults, or config behaviour.
