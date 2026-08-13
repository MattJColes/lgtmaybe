## Context

See `proposal.md` for the product decision. The current Action subscribes to every review-comment reply, builds the full provider/GitHub context, and asks the model to converse. The trustworthy lifecycle already exists elsewhere: a pushed commit triggers incremental review, and resolve-on-fix replies only after the finding is absent from the new code.

The reply feature shares one GraphQL posting primitive with resolve-on-fix and one identity safety case with the Action wrapper. Those shared pieces must remain while the speculative conversation path is deleted.

## Goals / Non-Goals

**Goals:**

- Make human finding-thread comments silent and free of provider work.
- Hard-remove the public reply configuration and workflow trigger.
- Preserve incremental verification and its resolved-thread message.
- Make stale workflow events safe during migration.

**Non-Goals:**

- Change `/ask`, finding fingerprints, incremental scope, or resolution criteria.
- Infer acceptance or resolution from comment text.
- Keep a deprecated configuration alias.

## Decisions

### Short-circuit stale events before configuration

The Action entrypoint will inspect `GITHUB_EVENT_NAME` before reading inputs, loading `.lgtmaybe.yml`, or constructing runtime dependencies. `pull_request_review_comment` returns success immediately. This ordering guarantees the compatibility no-op even when a stale workflow points at a repository whose config still contains the now-removed option.

**Alternative considered:** Remove the event branch and let it fall through. Rejected because the current default branch treats unknown events as review events, which can trigger an unintended paid review.

### Hard-remove the public option

Delete `answer_replies` from `ReviewConfig`, `action.yml`, generated schemas, reference docs, and tests. Strict validation intentionally rejects old config. This is a breaking release; no compatibility alias remains.

### Delete only reply-specific code

Delete the reply model prompt, untrusted reply wrapper, event handler, inbound comment-to-thread lookup, and their tests. Keep `reply_in_thread` and its GraphQL test because resolve-on-fix calls it after verified resolution. Update the posting spec and comments to make that single purpose explicit.

### Remove shipped triggers, retain identity safety

Starter workflows and documentation will remove `pull_request_review_comment` and its author guard. The identity bootstrap will retain its non-default-branch classification for that event so a stale workflow never attempts to mint branded credentials before the container reaches the no-op.

## Risks / Trade-offs

- **Existing config fails after upgrade** → ship as a major breaking change and name the two-line migration: delete `answer_replies` and the review-comment trigger.
- **A stale workflow still starts a runner** → the early no-op prevents provider/GitHub work; removing the trigger eliminates the runner on the next workflow edit.
- **Deleting the GraphQL reply helper breaks resolution** → retain focused resolve-on-fix and gateway tests around `reply_in_thread`.

## Migration Plan

Release as the next major version. Migration requires deleting `answer_replies` from `.lgtmaybe.yml` or Action inputs and removing `pull_request_review_comment` from the workflow `on:` block and job condition. Rollback restores the prior Action version and those two settings; no stored data changes.
