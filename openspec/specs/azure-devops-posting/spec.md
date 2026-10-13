# azure-devops-posting Specification

## Purpose

The Azure DevOps Repos adapter (`azure_devops/gateway.py`): the fourth forge,
and the one with no unified-diff endpoint. This spec records how a pull request
URL and pipeline environment become a locator, how the diff is assembled from
file contents, how findings become positioned threads, and which optional
capabilities the adapter does not claim yet.

## Requirements

### Requirement: A pull request URL names organisation, project, and repository

The locator SHALL recognise Azure DevOps Services pull request URLs on
`dev.azure.com/{org}` and the legacy `{org}.visualstudio.com`, and SHALL carry
them as the decoded `organisation/project/repository` path. An omitted project
segment SHALL mean the project shares the repository's name; a segment that
decodes to contain `/` SHALL be refused, as SHALL the same URL tail on any
other host (Azure DevOps Server).
<!-- anchor: azure-devops.locator -->

#### Scenario: the legacy host
- **WHEN** the URL is `https://acme.visualstudio.com/Proj/_git/api/pullrequest/7`
- **THEN** the locator's repo is `acme/Proj/api`

#### Scenario: a project name with a space
- **WHEN** the URL is `https://dev.azure.com/acme/My%20Project/_git/api/pullrequest/3`
- **THEN** the locator's repo is `acme/My Project/api` and its number is 3

#### Scenario: an on-premises collection
- **WHEN** the URL's host is not `dev.azure.com` or `*.visualstudio.com`
- **THEN** parsing fails naming Azure DevOps Server as unsupported

### Requirement: The diff is assembled from iteration changes

`get_pr_context` SHALL diff the latest iteration's changes against the merge
base (`commonRefCommit`) through the Items API only, raising `DiffUnavailable`
when the merge base is missing or the file cap is passed. Unreviewable files
SHALL NOT be fetched; they, oversized sides, and non-UTF-8 text SHALL appear as
hunkless binary patches. Control-character paths SHALL be left out, and a
rename without a usable `originalPath` SHALL be diffed as an edit in place.
<!-- anchor: azure-devops.context -->

#### Scenario: a lockfile changes
- **WHEN** `package-lock.json` is in the change list
- **THEN** it is listed as changed with a binary patch and its contents are never requested

#### Scenario: a sprawling change
- **WHEN** the latest iteration lists more than 300 changed files
- **THEN** the review is disclosed as not run rather than run on part of the change

#### Scenario: an iteration without commits
- **WHEN** the latest iteration names no source commit
- **THEN** the adapter raises rather than reviewing an empty diff

#### Scenario: no merge base
- **WHEN** the latest iteration has a target commit but no `commonRefCommit`
- **THEN** `DiffUnavailable` names the missing merge base and nothing is diffed against the target

#### Scenario: a rename without its old path
- **WHEN** a rename entry has no `originalPath`
- **THEN** the new path is read at both commits, and a 404 at the merge base is logged

### Requirement: A file that cannot be fetched degrades alone

Each Items fetch SHALL be retried a bounded number of times when it answers 429
or 5xx, times out, or drops its connection, honouring `Retry-After` up to a
cap. A side that still fails SHALL render its file as a hunkless binary patch,
never a fabricated diff, logged with path, side and reason, and named in the
summary under "Files not reviewed". A 401 or 403 SHALL fail the review: Azure
Repos has no per-file read permission, so it means the token is wrong.
<!-- anchor: azure-devops.fetch -->

#### Scenario: a throttled file
- **WHEN** a fetch answers 429 with `Retry-After: 2` and then succeeds
- **THEN** the adapter waits two seconds and diffs the file normally

#### Scenario: a file that never comes back
- **WHEN** every attempt at one file answers 503
- **THEN** only that file is shown as binary and named in the summary; the rest are reviewed

### Requirement: Dependency manifests are read within byte ceilings

With `set_scan_manifests(True)`, `get_pr_context` SHALL put the head text of
each changed dependency manifest in `scan_contents`, up to 8 MB per manifest
and 32 MB across all of them, reading them one at a time. A manifest past
either ceiling, binary, or failing to fetch SHALL be skipped and logged, never
failing the review.
<!-- anchor: azure-devops.scan -->

#### Scenario: the aggregate ceiling is reached
- **WHEN** a second lockfile would take the total past 32 MB
- **THEN** it is left out of `scan_contents` and named in the log

### Requirement: Rendered diffs agree with the shared parser

A diff rendered from two file texts SHALL be in `git diff` form so the shared
parser, commentable-line index, and file splitter read it unchanged: `/dev/null`
for an added or deleted side, `rename from`/`rename to` for a rename, lines
split on newline only, and every content line behind a diff marker so file text
can never forge a header.
<!-- anchor: azure-devops.render -->

#### Scenario: hostile file content
- **WHEN** an added file's text contains `diff --git` and `@@` lines
- **THEN** the parser still sees exactly one file and treats them as added lines

### Requirement: Each finding is its own positioned thread

Every inline finding SHALL be posted as an `active` thread whose
`threadContext` carries the file path and a one-line `rightFileStart`/
`rightFileEnd` range for the new side or `leftFileStart`/`leftFileEnd` for the
old side. A finding SHALL be demoted into the summary when it is not confidently
anchored, does not land on a commentable diff line, or Azure DevOps rejects its
thread.
<!-- anchor: azure-devops.positions -->

#### Scenario: a removed line
- **WHEN** a finding carries side LEFT
- **THEN** its thread sets `leftFileStart` and no right-file range

### Requirement: The summary lives in an edited general thread

The summary, description, and diagram SHALL each be a `closed` general thread
whose first comment carries its own marker family and is edited in place on a
re-run. Only our own identity's threads SHALL be edit targets; with the
identity unknown, the host's author check decides. A 403 or 404 SHALL move on
to the next candidate, then to a new thread; any other failed edit SHALL fail
the review, so an auth error surfaces.
<!-- anchor: azure-devops.upsert -->

#### Scenario: a re-run
- **WHEN** a review runs twice on the same pull request
- **THEN** the summary thread's first comment is edited and no second summary is created

#### Scenario: someone else plants the marker
- **WHEN** another user's thread carries the summary marker
- **THEN** it is never edited, and our own summary thread is

### Requirement: Findings are de-duplicated against live threads

Before posting, the adapter SHALL drop each finding whose hidden id appears in
the first comment of a live, non-`fixed` thread written by the token's own
identity (`connectionData`'s `authenticatedUser.id`, resolved once), consuming
each id once. A thread by anyone else SHALL NOT suppress a finding, and when the
identity cannot be read no thread SHALL. A won't-fix or closed thread of ours
SHALL keep suppressing its finding; a fixed one SHALL NOT.
<!-- anchor: azure-devops.dedupe -->

#### Scenario: a fixed finding returns
- **WHEN** a finding's earlier thread is `fixed` and the finding is produced again
- **THEN** a new thread is posted for it

#### Scenario: a planted finding id
- **WHEN** the pull request's author opens a thread carrying an expected finding's id
- **THEN** the finding is still posted

### Requirement: Tokens choose their auth scheme and stay private

The adapter SHALL send `AZURE_DEVOPS_TOKEN` as basic auth with an empty user
name, and otherwise `SYSTEM_ACCESSTOKEN` as a bearer token, preferring the
former when both are set. Tokens SHALL travel only in the `Authorization` header,
never in a URL or a log line. Every request to the versioned Git and pull
request APIs SHALL pin `api-version=7.1`; `connectionData`, a collection-level
endpoint with no confirmed version string, SHALL be sent without one.
<!-- anchor: azure-devops.auth -->

#### Scenario: only the pipeline token is set
- **WHEN** `SYSTEM_ACCESSTOKEN` is set and `AZURE_DEVOPS_TOKEN` is not
- **THEN** requests carry `Authorization: Bearer <token>`

### Requirement: Azure Pipelines identifies the pull request

The `azure-pipelines` command SHALL build the pull request URL from
`SYSTEM_COLLECTIONURI`, `SYSTEM_TEAMPROJECT`, `BUILD_REPOSITORY_NAME`, and
`SYSTEM_PULLREQUEST_PULLREQUESTID`, naming whichever is missing, and SHALL
refuse a build of a repository whose `BUILD_REPOSITORY_PROVIDER` is not Azure
Repos. An unset or empty provider, which only a manual run produces, SHALL be
treated as Azure Repos.
<!-- anchor: azure-devops.pipelines -->

#### Scenario: a CI build that is not for a pull request
- **WHEN** `SYSTEM_PULLREQUEST_PULLREQUESTID` is unset
- **THEN** the command fails naming that variable and the build validation policy

#### Scenario: the command is run by hand
- **WHEN** `BUILD_REPOSITORY_PROVIDER` is unset and the other variables are present
- **THEN** the pull request URL is built as for Azure Repos

### Requirement: Unbuilt capabilities are not claimed

The adapter SHALL declare file contents, description, diagram, and conversation
reading, and SHALL NOT claim incremental review, thread resolution, labels,
checks, feedback, or base checkout, so the caller degrades to a full review
without them.
<!-- anchor: azure-devops.capabilities -->

#### Scenario: thread resolution is probed
- **WHEN** a caller checks the adapter for thread resolution
- **THEN** the check fails and resolve-on-fix is skipped
