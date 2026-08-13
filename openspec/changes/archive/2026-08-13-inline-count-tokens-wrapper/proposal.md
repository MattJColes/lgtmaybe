## Why

The provider has a private wrapper that only lazily imports and delegates to the engine token counter. Moving that lazy import to its sole caller preserves import behaviour while removing an unnecessary symbol.

## What Changes

- Import `count_tokens` lazily inside `_with_cache_control`.
- Call `count_tokens` directly for cache-threshold calculations.
- Delete the `_count_tokens` delegating wrapper.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is a behaviour-preserving refactor, so `skip_specs` is enabled.

## Impact

Only `src/lgtmaybe/providers/litellm_provider.py` changes. Provider request shape, token accounting, public APIs, and dependencies remain unchanged.
