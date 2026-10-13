## Context

- `ReviewGateway` is three methods; everything richer is an optional
  `Supports*` protocol the CLI probes for. An adapter can ship useful before it
  ships complete.
- `core/diff.py`, `core/diffparse.py`, `core/comment.py` and `core/findings.py`
  are shared by every adapter. A new adapter must not reimplement them.
- Azure DevOps has no unified-diff endpoint. A pull request has iterations (one
  per push); each iteration lists its changed paths, and the Items API returns
  any path's content at any commit.
- Azure DevOps comments live in threads. A thread may carry a `threadContext`
  (file path plus right-file or left-file line ranges) or none (a general
  thread). Only a comment's author may edit it.

## Decisions

### D1. Assemble the diff, then reuse the shared parser

The latest iteration's changes with `$compareTo=0` are the change against the
merge base (`commonRefCommit`), the same three-dot view GitHub's pull request
diff shows. For each reviewable file the adapter fetches the merge-base and
source-commit text concurrently and renders a `git diff` patch with
`core/diffbuild.py` (`difflib`, three lines of context). The output is a normal
unified diff, so `build_commentable_lines`, `split_by_file` and the engine read
it unchanged. Rendering is host-neutral, so it sits in `core`, not the adapter.

Alternatives: post-process Azure's per-file diff API (it returns block offsets,
not text, and still needs the file contents), or review whole files (loses the
changed-line contract every lens depends on).

### D2. Bound what is fetched, and say so

- Unreviewable files (`is_reviewable` false) are named with a hunkless binary
  patch and never fetched.
- A side over 1 MB, or containing NUL, or not UTF-8, renders as binary. The body
  is streamed and abandoned at the cap. An oversized text file therefore reaches
  the engine as "changed, not shown" rather than through the per-file line-cap
  notice GitHub's diff would trigger.
- More than 300 changed files (or a further page) raises `DiffUnavailable`,
  mirroring GitHub's own 300-file diff limit, so the CLI discloses a review that
  did not happen instead of truncating silently.
- A path holding a control character is left out and logged: a newline in a
  filename chosen by the PR author would otherwise forge diff header lines.
- No merge base (`commonRefCommit`) raises `DiffUnavailable`. Falling back to
  the target branch head would pull in other merged pull requests' changes and
  anchor findings on lines this one never touched.
- Azure DevOps throttles readily, so an Items fetch answering 429 or 5xx, or
  timing out, is retried three times, honouring `Retry-After` up to 10 seconds.
  A side that still fails degrades only its file to a binary patch, logged and
  named in the summary under "Files not reviewed" (`core.comment`). A 401 or
  403 fails the review: Azure Repos has no per-file read permission, so it
  means the token is wrong, not the file.
- Dependency manifests for scanning are read one at a time, up to 8 MB each and
  32 MB together; any past either ceiling is skipped and logged.
- `connectionData` is sent without `api-version`: it is a collection-level
  endpoint outside the versioned Git API, and no version string for it could
  be confirmed.

### D3. Threads, with explicit statuses

Each finding is an `active` thread with a one-line right-file (RIGHT) or
left-file (LEFT) range, offset 1. Positions use only `threadContext`, without
`pullRequestThreadContext` iteration pinning, which the API treats as optional.
The summary, description, diagram, and notices are `closed` general threads, so
a "resolve all comments" branch policy never waits on them, while findings can
still hold a merge, as that policy intends. A rejected finding thread is
demoted into the summary.

### D4. Trust only our own threads

A finding's hidden ids derive from its path and title, both public, so anyone
who can comment on the pull request (its author included) could plant a thread
carrying them and suppress the finding. The adapter therefore resolves the
token's own identity once per gateway (`GET {collection}/_apis/connectionData`,
`authenticatedUser.id`; it answers for a personal access token and for
`System.AccessToken`, the latter as the project build service) and compares it
with each root comment's `author.id`.

- De-duplication reads hidden ids only from live, non-`fixed` threads we wrote:
  a resolved finding that returns is reported again (GitLab's rule), while
  won't-fix, by-design and closed dispositions of ours keep suppressing it. If
  the identity cannot be read, no thread is trusted: a finding may post twice,
  but is never hidden.
- The summary, description and diagram are edited only in a thread we opened,
  found by its marker. With the identity unknown every marked thread is tried,
  which is still safe because Azure DevOps lets only a comment's author edit
  it; a 403 or 404 moves on to the next candidate, and with none editable a new
  thread is opened.

### D5. Two token shapes, one variable list

`core.forge.token_env_vars` returns every variable a forge's token may arrive
in, most explicit first: `AZURE_DEVOPS_TOKEN` then `SYSTEM_ACCESSTOKEN`. The
builder sends a personal access token as basic auth with an empty user and the
pipeline token as a bearer token. `token_env_var` keeps returning the primary
variable, so existing callers are unchanged.

### D6. Locator shape

`PRLocator.repo` carries `organisation/project/repository`, each segment
percent-decoded, rather than new locator fields: no other forge needs them, and
none of the three segments may contain `/`. The gateway re-encodes each segment.
A URL with the project segment omitted (Azure DevOps does this when the project
and repository share a name) maps the project to the repository's name.

## Risks / Trade-offs

- Not verified against a live organisation in this change. Request and response
  shapes follow the 7.1 REST reference, and the tests fake them. Hidden
  `<!-- -->` markers are assumed to stay hidden in Azure DevOps Markdown, and
  raw file content is requested from the Items API with
  `Accept: application/octet-stream`.
- `difflib` can place a hunk differently from `git diff` among equally good
  alignments. Line numbers stay exact, which is what positioning needs.
- No `\ No newline at end of file` markers: a change that only adds or removes
  the final newline renders no hunk.

## Non-Goals

Incremental review, thread resolution, labels, commit statuses, feedback, base
checkout, slash commands, the automatic change overview from `azure-pipelines`,
and Azure DevOps Server. Each is a "not yet"; none is claimed.
