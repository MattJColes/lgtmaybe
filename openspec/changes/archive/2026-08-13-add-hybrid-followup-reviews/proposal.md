## Why

The current reviewed-SHA watermark advances before the independently posted change diagram exists, and later incremental runs infer that earlier findings are fixed merely because the model did not reproduce them. A completed run needs one durable definition, and follow-up runs need to verify earlier findings explicitly without paying for another full review.

## What Changes

- Record a completed head only after a non-partial review result and, when automatic diagrams are enabled, a current-head diagram have both posted.
- Refresh automatic diagrams on synchronize events and use their head marker as the completion watermark.
- Review only the new compare diff on later heads while explicitly classifying existing findings as fixed, still open, or uncertain.
- Resolve only findings explicitly classified fixed; retain still-open and uncertain threads without repetitive replies.
- Make an already-completed same-head run a no-op and safely fall back to a full review for missing markers, force-pushes, compare failures, and incomplete attempts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-posting`: Define durable end-to-end completion markers and explicit finding validation/resolution for follow-up reviews.
- `cli-and-local`: Orchestrate same-head no-op, hybrid incremental review, and required per-head diagram refresh.

## Impact

The CLI review orchestration, structured model contracts, GitHub REST/GraphQL adapter, diagram upsert markers, incremental tests, Action-flow tests, and the two living specifications above change. The frozen GitHub port and user configuration remain unchanged, and no dependency or external datastore is added.
