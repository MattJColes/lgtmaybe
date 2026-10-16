## ADDED Requirements

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
