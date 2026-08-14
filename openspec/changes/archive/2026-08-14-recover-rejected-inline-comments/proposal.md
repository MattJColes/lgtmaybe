## Why

On an incremental re-review, GitHub can reject one newly generated inline
comment with `422 Unprocessable Entity` after lgtmaybe has already computed the
review. The current adapter aborts at that comment, so later findings are not
posted and the rejected finding is absent from the review body.

## What Changes

- Treat a 422 from an individual rerun comment as a placement failure rather
  than a total review failure.
- Continue posting later inline findings and render every rejected finding in
  the existing review's `Additional findings` section.
- Log GitHub's validation details with the rejected path and position while
  keeping credentials and comment prose out of logs.
- Keep first-review batching and non-422 failure behavior unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-posting`: Preserve rerun findings that GitHub rejects inline.

## Impact

- Rerun posting in `src/lgtmaybe/github/rest_gateway.py`.
- Focused HTTP-boundary coverage in `tests/github/test_incremental.py`.
- No public API, configuration, dependency, or stored-marker changes.
