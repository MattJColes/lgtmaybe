## Why

Scanner-specific prompt variants currently subtract exact prose with `str.replace`, which silently stops narrowing the prompt when the source wording changes.

## What Changes

- Compose security, dependency-health, and merged code-health sections from optional prompt fragments.
- Keep scanner carve-out behaviour and wording covered by tests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. Prompt behaviour remains unchanged, so `skip_specs` is enabled.

## Impact

Prompt construction and its focused tests change; model contracts and configuration do not.
