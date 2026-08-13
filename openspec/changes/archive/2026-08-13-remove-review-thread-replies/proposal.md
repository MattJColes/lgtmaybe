## Why

Automatically answering every human reply in a finding thread duplicates commit-scoped re-review, spends provider tokens, and can argue with an explanation before the changed code is evaluated. Human comments are not evidence that a finding is fixed; the existing synchronize review and resolve-on-fix path already provide the trustworthy feedback loop.

## What Changes

- Remove model-generated answers to `pull_request_review_comment` events.
- Keep stale review-comment events as an early successful no-op so old workflows cannot trigger a paid review during migration.
- **BREAKING** Remove `ReviewConfig.answer_replies`, the `answer_replies` Action input, and the corresponding environment/config surface.
- Remove the obsolete event trigger and reply arm from starter workflows and documentation.
- Preserve verified resolve-on-fix, including its GraphQL `✅ Looks resolved.` thread reply.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Remove automatic finding-thread conversations; stale review-comment events perform no work.
- `core-contracts`: Remove the public `answer_replies` configuration field and default.
- `github-posting`: Narrow GraphQL thread replies to the verified resolve-on-fix path.

## Impact

The change removes reply-specific CLI, injection, gateway lookup, configuration, tests, workflow triggers, and documentation. Users must delete `answer_replies` from config and remove `pull_request_review_comment` from custom workflows. `/ask`, incremental review, finding resolution, and the shared GraphQL reply primitive used after verified fixes remain unchanged. The public configuration removal requires a breaking major release.
