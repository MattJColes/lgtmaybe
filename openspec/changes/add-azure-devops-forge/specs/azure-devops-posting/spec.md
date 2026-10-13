## ADDED Requirements

### Requirement: A pull request URL names organisation, project, and repository
<!-- anchor: azure-devops.locator -->

The locator SHALL recognise Azure DevOps Services pull request URLs on
`dev.azure.com/{org}` and the legacy `{org}.visualstudio.com`, and SHALL carry
them as the decoded `organisation/project/repository` path. An omitted project
segment SHALL mean the project shares the repository's name; a segment that
decodes to contain `/` SHALL be refused, as SHALL the same URL tail on any
other host (Azure DevOps Server).

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
<!-- anchor: azure-devops.context -->

`get_pr_context` SHALL diff the latest iteration's changes against the merge
base (`commonRefCommit`), reading each side through the Items API only.
Unreviewable files SHALL NOT be fetched; they, oversized sides, and non-UTF-8
text SHALL appear as hunkless binary patches. A path holding a control
character SHALL be left out, and a change list past the file cap SHALL raise
`DiffUnavailable`.

#### Scenario: a lockfile changes
- **WHEN** `package-lock.json` is in the change list
- **THEN** it is listed as changed with a binary patch and its contents are never requested

#### Scenario: a sprawling change
- **WHEN** the latest iteration lists more than 300 changed files
- **THEN** the review is disclosed as not run rather than run on part of the change

#### Scenario: an iteration without commits
- **WHEN** the latest iteration names no source or merge-base commit
- **THEN** the adapter raises rather than reviewing an empty diff

### Requirement: Rendered diffs agree with the shared parser
<!-- anchor: azure-devops.render -->

A diff rendered from two file texts SHALL be in `git diff` form so the shared
parser, commentable-line index, and file splitter read it unchanged: `/dev/null`
for an added or deleted side, `rename from`/`rename to` for a rename, lines
split on newline only, and every content line behind a diff marker so file text
can never forge a header.

#### Scenario: hostile file content
- **WHEN** an added file's text contains `diff --git` and `@@` lines
- **THEN** the parser still sees exactly one file and treats them as added lines

### Requirement: Each finding is its own positioned thread
<!-- anchor: azure-devops.positions -->

Every inline finding SHALL be posted as an `active` thread whose
`threadContext` carries the file path and a one-line `rightFileStart`/
`rightFileEnd` range for the new side or `leftFileStart`/`leftFileEnd` for the
old side. A finding SHALL be demoted into the summary when it is not confidently
anchored, does not land on a commentable diff line, or Azure DevOps rejects its
thread.

#### Scenario: a removed line
- **WHEN** a finding carries side LEFT
- **THEN** its thread sets `leftFileStart` and no right-file range

### Requirement: The summary lives in an edited general thread
<!-- anchor: azure-devops.upsert -->

The summary, description, and diagram SHALL each be a `closed` general thread
whose first comment carries its own hidden marker family and is edited in place
on a re-run. Only threads whose first comment the token's own identity wrote
SHALL be edit targets; when the identity is unknown, the host's author check
decides. An edit refused with 403 or 404 SHALL move on to the next candidate,
and a new thread SHALL be posted when none can be edited, never failing the
review.

#### Scenario: a re-run
- **WHEN** a review runs twice on the same pull request
- **THEN** the summary thread's first comment is edited and no second summary is created

#### Scenario: someone else plants the marker
- **WHEN** another user's thread carries the summary marker
- **THEN** it is never edited, and our own summary thread is

### Requirement: Findings are de-duplicated against live threads
<!-- anchor: azure-devops.dedupe -->

Before posting, the adapter SHALL drop each finding whose hidden id appears in
the first comment of a live, non-`fixed` thread written by the token's own
identity (`connectionData`'s `authenticatedUser.id`, resolved once), consuming
each id once. A thread by anyone else SHALL NOT suppress a finding, and when the
identity cannot be read no thread SHALL. A won't-fix or closed thread of ours
SHALL keep suppressing its finding; a fixed one SHALL NOT.

#### Scenario: a fixed finding returns
- **WHEN** a finding's earlier thread is `fixed` and the finding is produced again
- **THEN** a new thread is posted for it

#### Scenario: a planted finding id
- **WHEN** the pull request's author opens a thread carrying an expected finding's id
- **THEN** the finding is still posted

### Requirement: Tokens choose their auth scheme and stay private
<!-- anchor: azure-devops.auth -->

The adapter SHALL send `AZURE_DEVOPS_TOKEN` as basic auth with an empty user
name, and otherwise `SYSTEM_ACCESSTOKEN` as a bearer token, preferring the
former when both are set. Tokens SHALL travel only in the `Authorization` header,
never in a URL or a log line, and every request SHALL pin `api-version=7.1`.

#### Scenario: only the pipeline token is set
- **WHEN** `SYSTEM_ACCESSTOKEN` is set and `AZURE_DEVOPS_TOKEN` is not
- **THEN** requests carry `Authorization: Bearer <token>`

### Requirement: Azure Pipelines identifies the pull request
<!-- anchor: azure-devops.pipelines -->

The `azure-pipelines` command SHALL build the pull request URL from
`SYSTEM_COLLECTIONURI`, `SYSTEM_TEAMPROJECT`, `BUILD_REPOSITORY_NAME`, and
`SYSTEM_PULLREQUEST_PULLREQUESTID`, naming whichever is missing, and SHALL
refuse a build of a repository whose `BUILD_REPOSITORY_PROVIDER` is not Azure
Repos.

#### Scenario: a CI build that is not for a pull request
- **WHEN** `SYSTEM_PULLREQUEST_PULLREQUESTID` is unset
- **THEN** the command fails naming that variable and the build validation policy

### Requirement: Unbuilt capabilities are not claimed
<!-- anchor: azure-devops.capabilities -->

The adapter SHALL declare file contents, description, diagram, and conversation
reading, and SHALL NOT claim incremental review, thread resolution, labels,
checks, feedback, or base checkout, so the caller degrades to a full review
without them.

#### Scenario: thread resolution is probed
- **WHEN** a caller checks the adapter for thread resolution
- **THEN** the check fails and resolve-on-fix is skipped
