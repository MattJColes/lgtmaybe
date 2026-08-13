## Why

Two modules exist only to hold a single runtime type or three provider defaults, adding files and import hops without creating independent behaviour.

## What Changes

- Move `RuntimeOptions` into the CLI package module that owns its construction.
- Move provider defaults into `factory.py` and update consumers.
- Delete the two single-purpose modules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is an import-location refactor, so `skip_specs` is enabled.

## Impact

CLI and provider imports change; public `lgtmaybe.cli.RuntimeOptions` and provider behaviour remain stable.
