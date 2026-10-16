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
updating one never clobbers another. Only a comment the posting identity
(`GET /api/v1/user`) wrote SHALL be edited; when that identity is unknown a new
comment is posted instead.
<!-- anchor: gitea.upsert -->

#### Scenario: a re-run edits rather than duplicates
- **WHEN** a review runs a second time on the same pull request
- **THEN** the existing summary comment is edited and no second one is posted

### Requirement: Findings are de-duplicated before posting

Because a submitted review cannot be amended, the adapter SHALL read the hidden
finding ids already present in the reviews the posting identity wrote and drop
any finding matching one before posting — anyone can paste a hidden id, so
another author's copy SHALL NOT suppress a finding. Failure to read them SHALL be non-fatal — a
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

#### Scenario: Forgejo offers no resolve API either
- **WHEN** the adapter serves a Forgejo pull request
- **THEN** thread resolution is still not claimed, because Forgejo resolves a
  conversation only through a browser-session web route

### Requirement: Forgejo is served by the Gitea adapter

A Forgejo pull request SHALL be reviewed and posted through the Gitea adapter,
with no separate forge value, because Forgejo serves the same `/api/v1` and the
same `/{owner}/{repo}/pulls/{n}` URL shape. The Actions entrypoint SHALL build
the pull request URL from `FORGEJO_SERVER_URL` when it is set, else from
`GITHUB_SERVER_URL`, and SHALL NOT apply the GitHub Enterprise Server refusal to
a run that sets `FORGEJO_SERVER_URL`.
<!-- anchor: gitea.forgejo-host -->

#### Scenario: a Forgejo run posts to its own instance
- **WHEN** a Forgejo Actions run sets `FORGEJO_SERVER_URL` to
  `https://codeberg.org` for pull request 7 in `owner/repo`
- **THEN** the review targets `https://codeberg.org/owner/repo/pulls/7` through
  the Gitea adapter

#### Scenario: an older runner sets only the GitHub aliases
- **WHEN** a forgejo-runner before v7 sets `GITHUB_SERVER_URL` but not
  `FORGEJO_SERVER_URL`
- **THEN** the URL is built from `GITHUB_SERVER_URL`, as for Gitea

#### Scenario: a Forgejo API URL is not GitHub Enterprise Server
- **WHEN** `FORGEJO_SERVER_URL` is set
- **THEN** the run is not refused as GitHub Enterprise Server, whatever
  `GITHUB_API_URL` holds

### Requirement: The Actions event is read under either runtime name

The `action` entrypoint SHALL read the event name and payload path from
`GITHUB_EVENT_NAME` and `GITHUB_EVENT_PATH`, falling back to
`FORGEJO_EVENT_NAME` and `FORGEJO_EVENT_PATH`, so it depends on neither
forgejo-runner's aliases nor their absence. With neither path set it SHALL fail
naming both variables.
<!-- anchor: gitea.forgejo-event-env -->

#### Scenario: a runner sets only the Forgejo names
- **WHEN** a run sets `FORGEJO_EVENT_NAME` and `FORGEJO_EVENT_PATH` but no
  `GITHUB_*` event variables
- **THEN** the event is routed and reviewed exactly as with the `GITHUB_` names

#### Scenario: no event path at all
- **WHEN** neither `GITHUB_EVENT_PATH` nor `FORGEJO_EVENT_PATH` is set
- **THEN** the run exits non-zero naming both

### Requirement: The Forgejo starter workflow never runs pull-request code

The supplied Forgejo workflow SHALL trigger on `pull_request_target` and
`issue_comment` only, use the lgtmaybe image as its job container, contain no
`uses:` step, and run only `python -m lgtmaybe action`. As shipped, its forge
token SHALL be mapped from a `LGTMAYBE_`-prefixed secret, never the automatic
job token, because that token cannot check a commenter's permission. Mapping
the job token is a user's documented opt-in edit.
<!-- anchor: gitea.forgejo-workflow -->

#### Scenario: a pull request edits the workflow
- **WHEN** a pull request changes `.forgejo/workflows/lgtmaybe.yml`
- **THEN** the review runs the base branch's copy, because the trigger is
  `pull_request_target`

#### Scenario: the token secret is named
- **WHEN** the starter workflow is read
- **THEN** `GITEA_TOKEN` comes from a `LGTMAYBE_`-prefixed secret

#### Scenario: a user opts in to the job token
- **WHEN** a user maps `GITEA_TOKEN` to `secrets.FORGEJO_TOKEN`
- **THEN** reviews still post and re-runs still de-duplicate, while slash
  commands are ignored because the commenter's permission cannot be read

### Requirement: lgtmaybe's own comments cannot cancel a slash command

The supplied Forgejo workflows SHALL give each `issue_comment` run whose body
does not start with `/` a concurrency group of its own, because a comment
posted with a personal token starts a run and workflow-level concurrency
applies before the job's `if`. Slash commands SHALL still share a group per
pull request.
<!-- anchor: gitea.forgejo-comment-concurrency -->

#### Scenario: a slash command posts its reply
- **WHEN** `/review` posts its summary comment while the command's run is
  still going
- **THEN** the run that comment starts is skipped in its own group and the
  command finishes
