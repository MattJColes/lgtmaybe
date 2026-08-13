## Why

The default split-prefix prompt is already safe on every provider, while the disabled-cache rollback path doubles prompt construction and test surface without preserving a distinct capability.

## What Changes

- **BREAKING**: remove the `prompt_cache` configuration field, CLI flag, and Action input.
- Always assemble review and reflection prompts using the split-prefix shape.
- Remove the legacy monolithic prompt builders and provider opt-out branches.
- Keep cache breakpoints automatic on supported routes and plain-message merging elsewhere.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `prompt-and-lenses`: split-prefix prompt composition becomes unconditional rather than configurable.

## Impact

Prompt construction, provider setup, CLI/Action configuration, generated reference documentation, and prompt-cache tests change. Routes without explicit cache support still receive merged plain user messages; users who previously disabled caching may see different cache usage and token accounting.
