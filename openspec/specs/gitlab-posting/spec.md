# gitlab-posting Specification

## Purpose

The GitLab REST adapter (`gitlab/gateway.py`): the forge whose model genuinely
differs rather than renaming things. No batched review object, positions that
carry the merge request's diff refs, and thread resolution over plain REST
instead of GraphQL.

## Requirements

### Requirement: A project is addressed by its encoded path

The adapter SHALL address a project by its URL-encoded full path, so an
arbitrarily nested group path survives as a single URL segment, and SHALL take
the host from the merge request URL rather than assuming gitlab.com — self-hosted
GitLab is the common case.
<!-- anchor: gitlab.project -->

#### Scenario: a nested group path is encoded
- **WHEN** the project is "group/subgroup/project"
- **THEN** requests address it as "group%2Fsubgroup%2Fproject"

### Requirement: Context comes from the API, never a checkout

`get_pr_context` SHALL fetch the diff, metadata, and head text of reviewable
files through the GitLab REST API only — merge request code is never checked out
or executed. The adapter SHALL cache the merge request's `base_sha`,
`start_sha`, and `head_sha`, because a positioned discussion cannot be created
without all three, and SHALL fail loudly when they are absent rather than
reviewing an empty diff.
<!-- anchor: gitlab.context -->

#### Scenario: metadata without diff refs is fatal
- **WHEN** the API returns a merge request payload carrying no diff refs
- **THEN** the adapter raises rather than posting positionless findings

### Requirement: Each finding is its own positioned discussion

GitLab has no batched review object, so every inline finding SHALL be posted as
its own discussion carrying a `position` object that names the old and new path,
the relevant old or new line translated from lgtmaybe's RIGHT/LEFT side
vocabulary, and the three cached diff refs. A rejected position SHALL be logged
and skipped rather than failing the review, and a finding SHALL be demoted into
the summary when it is not confidently anchored, when it does not land on a real
commentable diff line, or when the diff refs are unknown.
<!-- anchor: gitlab.positions -->

#### Scenario: one unplaceable finding does not lose the others
- **WHEN** GitLab rejects a discussion's position
- **THEN** the remaining findings and the summary still post

### Requirement: The summary lives in an editable note

The summary SHALL be posted as a merge request note carrying the hidden marker,
upserted in place on a re-run, with disjoint marker families for the summary,
the description, and the diagram. Findings whose hidden ids are already present
on the merge request SHALL NOT be posted again.
<!-- anchor: gitlab.upsert -->

#### Scenario: a re-run edits rather than duplicates
- **WHEN** a review runs a second time on the same merge request
- **THEN** the existing summary note is edited and no second one is posted

### Requirement: Threads resolve over REST, only when validated

Resolve-on-fix SHALL reply in and then resolve only the discussions the caller
has explicitly validated as fixed, via the discussion resolve endpoint — GitLab
needs no GraphQL for this, unlike GitHub. With no validated allowlist installed,
nothing SHALL be resolved: GitLab exposes no "the lines moved" signal, so an
unvalidated close would have no evidence behind it. Resolving SHALL never fail
the review.
<!-- anchor: gitlab.resolve -->

#### Scenario: an unvalidated thread stays open
- **WHEN** a review completes with an empty validated allowlist
- **THEN** no discussion is resolved
