## Why

The `diff --git` header is ambiguous when a valid filename itself contains
` b/`. The greedy parser truncates `dir b/file.py` to `file.py`, breaking file
batching and inline coordinates.

## What Changes

- Use the unambiguous `--- a/` and `+++ b/` metadata lines for file paths.
- Keep `diff --git` lines only as patch boundaries.

## Capabilities

### Modified Capabilities

- `cli-and-local`: Preserve valid Git paths exactly through diff processing.

## Impact

- Shared diff parsing, incremental reviewed-path extraction, and focused tests.
