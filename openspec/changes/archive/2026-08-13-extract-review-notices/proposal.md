## Why

`LLMReviewEngine.review` ends with a large block that interleaves ten summary notices with the pipeline, obscuring the main flow and making transparency rules hard to review together.

## What Changes

- Move notice inputs into an immutable state object.
- Extract notice rendering into `_build_notices` and table-drive the repeated count-based notices.
- Keep every existing notice text and condition unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is behaviour-preserving, so `skip_specs` is enabled.

## Impact

Only engine organization and tests change.
