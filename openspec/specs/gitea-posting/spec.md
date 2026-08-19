# gitea-posting Specification

## Purpose

The Gitea REST adapter (`gitea/gateway.py`): the second forge lgtmaybe posts to,
and the one that proves the seam. Gitea's API mirrors GitHub's closely, so this
spec records only where it does not — an immutable review object, a different
position vocabulary, and the capabilities Gitea cannot serve at all.

## Requirements

### Requirement: Context comes from the API, never a checkout

`get_pr_context` SHALL fetch the diff, metadata, and head text of reviewable
files through the Gitea REST API only — pull-request code is never checked out
or executed. Head file text is returned base64-encoded and SHALL be decoded
before it reaches the engine.
<!-- anchor: gitea.context -->

#### Scenario: metadata without SHAs is fatal
- **WHEN** the API returns a payload carrying no base or head SHA
- **THEN** the adapter raises rather than reviewing an empty diff

### Requirement: The summary lives in an editable comment

A Gitea review object cannot be edited after submission, so the summary SHALL be
posted as an ordinary issue comment carrying the hidden marker, upserted in
place on a re-run, while the review object carries only inline comments. Each
comment family — summary, description, diagram — SHALL use a disjoint marker so
updating one never clobbers another.
<!-- anchor: gitea.upsert -->

#### Scenario: a re-run edits rather than duplicates
- **WHEN** a review runs a second time on the same pull request
- **THEN** the existing summary comment is edited and no second one is posted

### Requirement: Findings are de-duplicated before posting

Because a submitted review cannot be amended, the adapter SHALL read the hidden
finding ids already present in its earlier review comments and drop any finding
matching one before posting. Failure to read them SHALL be non-fatal — a
duplicated comment is a better outcome than a failed review.
<!-- anchor: gitea.dedupe -->

#### Scenario: a repeated finding is not posted twice
- **WHEN** a re-run produces a finding whose id is already on the pull request
- **THEN** no new inline comment is created for it

### Requirement: Positions translate at the adapter boundary

Inline comments SHALL be anchored with Gitea's `new_position` (new-file line) or
`old_position` (old-file line), translated from lgtmaybe's internal RIGHT/LEFT
side vocabulary. A finding that is not confidently anchored, or that does not
land on a real commentable diff line, SHALL be demoted into the summary body
rather than posted on a line the reviewer cannot stand behind.
<!-- anchor: gitea.positions -->

#### Scenario: a left-side finding uses the old-file line
- **WHEN** a finding carries side LEFT
- **THEN** the posted comment sets `old_position` and not `new_position`

### Requirement: Unavailable capabilities are not claimed

The adapter SHALL declare only the optional capability protocols Gitea can
actually serve. Incremental review and review-thread resolution SHALL NOT be
claimed — Gitea's compare endpoint returns commit metadata rather than a unified
diff, and it has no thread-resolution API — so a caller degrades to a full
review instead of calling a method that cannot work.
<!-- anchor: gitea.capabilities -->

#### Scenario: thread resolution is absent
- **WHEN** a caller probes the adapter for thread resolution
- **THEN** the check fails and resolve-on-fix is skipped
