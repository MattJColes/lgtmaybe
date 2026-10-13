## MODIFIED Requirements

### Requirement: Threads resolve over REST, only when validated

Resolve-on-fix SHALL reply in and then resolve only the discussions the caller
has explicitly validated as fixed, via the discussion resolve endpoint — GitLab
needs no GraphQL for this, unlike GitHub. With no validated allowlist installed,
nothing SHALL be resolved. GitLab exposes no "the lines moved" flag, so the
adapter SHALL derive one: a discussion is outdated when a hunk of the straight
diff from the head it is positioned at to the current head covers its new-side
line, or its file was deleted; an unknown head, a LEFT-side note, or any compare
failure reads as not outdated. On an incremental run only discussions on files
the increment re-reviewed SHALL be resolved. Resolving SHALL never fail the
review.
<!-- anchor: gitlab.resolve -->

#### Scenario: an unvalidated thread stays open
- **WHEN** a review completes with an empty validated allowlist
- **THEN** no discussion is resolved

#### Scenario: a validated thread on a file the increment never touched
- **WHEN** an incremental run's allowlist names a discussion outside its files
- **THEN** that discussion stays open

## ADDED Requirements

### Requirement: Incremental review is commit-scoped and safe

A follow-up run SHALL review only the repository compare diff since the head
recorded in the summary note's watermark, reassembled into a unified diff. The
watermark SHALL be read only from a note the token's own user wrote, SHALL be
written only on the success path, and an unmarked post (a failure notice or a
partial run) SHALL keep the previous watermark rather than move it. Because
GitLab has no "ahead" status, the increment SHALL be used only when the commit
list chains through single parents from the head back to the watermark; a
force-push or rebase, a merge, an unknown SHA, a timed-out or truncated compare
(a withheld patch, or a file list at GitLab's smallest diff limit), an unsafe
path, or any API failure SHALL return no increment so the run is a full review.
<!-- anchor: gitlab.incremental -->

#### Scenario: a plain push after a complete review
- **WHEN** the head descends linearly from the watermark
- **THEN** only the commits since it are reviewed and the new head is stamped

#### Scenario: the branch was rebased
- **WHEN** the commit chain from the head does not reach the watermark
- **THEN** the whole merge request is reviewed

#### Scenario: a forged watermark
- **WHEN** a note by another user carries the summary marker and a watermark
- **THEN** it is ignored

### Requirement: Downvoted findings are read from award emoji

The adapter SHALL report the fingerprint of each of its finding discussions
whose first note carries a `thumbsdown` award emoji from a member with
Developer access or above. Any other reactor, and one whose membership cannot
be read, SHALL NOT count, and a note whose awards cannot be read SHALL be
skipped rather than fail the review.
<!-- anchor: gitlab.feedback -->

#### Scenario: a Reporter downvotes a finding
- **WHEN** the only 👎 comes from a member below Developer
- **THEN** the finding is not suppressed
