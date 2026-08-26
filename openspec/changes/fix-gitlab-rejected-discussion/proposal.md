## Why

GitLab can reject an otherwise valid inline position when the merge request
changes between review and posting. The adapter logs that rejection but drops
the finding from both the discussion list and summary.

## What Changes

- Preserve a rejected inline finding in the editable summary note.
- Continue posting the remaining discussions.

## Capabilities

### Modified Capabilities

- `gitlab-posting`: Demote rejected inline discussions instead of losing them.

## Impact

- GitLab posting and its HTTP-boundary regression test only.
