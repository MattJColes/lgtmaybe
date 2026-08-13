# cli-and-local Specification

## Purpose

The Click CLI (`cli/`), the slash-command router, local no-GitHub review of a
git diff (`local/`), and layered config (`config/`) — one engine behind
`review`, `comment`, and `action` entrypoints.
## Requirements
### Requirement: Review failures are loud

The `review` command SHALL surface any failure: a short "review failed"
comment is posted (when a PR is in play) and the CLI exits non-zero — never a
silent success.
<!-- anchor: cli.review-command -->

#### Scenario: provider call dies mid-review
- **WHEN** the review raises
- **THEN** the run exits non-zero with the error surfaced, and a failure
  comment is posted on the PR

### Requirement: One orchestrator behind every entrypoint

`run_review` SHALL orchestrate the shared flow — completed-head read, same-head no-op, incremental vs full decision, explicit earlier-finding validation, engine call, posting, and completion stamping — so `review`, `comment`, and `action` never duplicate review logic. Automatic synchronize runs SHALL use the hybrid incremental path; explicit `incremental: false` and `/review full` SHALL run a full review.
<!-- anchor: cli.run-review -->

#### Scenario: Action synchronize event
- **WHEN** the Action runs on `synchronize` with `incremental` unset after a completed review
- **THEN** the same orchestrator scans the new compare diff and validates earlier open findings

#### Scenario: reviewer forces a full review
- **WHEN** `/review full` runs after a completed review
- **THEN** completion state is ignored and the entire PR is reviewed again

### Requirement: A termination signal posts partial results

The CLI SHALL turn the first SIGINT/SIGTERM into a graceful wind-down that
posts what the review already produced. The handler sets the same state the
`max_review_seconds` deadline sets, so queued model calls are skipped, in-flight
ones finish, and the summary carries the partial-results notice — naming the
interruption rather than a ceiling nobody hit. It is installed by the CLI
entrypoint only (importing lgtmaybe as a library never touches a host's
handlers), is a no-op off the main thread or where the platform lacks the
signal, and restores the previous handler as it fires.
<!-- anchor: cli.graceful-interrupt -->

#### Scenario: the CI job is cancelled or exceeds timeout-minutes
- **WHEN** the runner signals the process mid-review
- **THEN** no further model calls are dispatched and the findings already
  produced post with a notice, instead of the run dying with nothing on the PR

#### Scenario: a second signal arrives
- **WHEN** the wind-down is under way and another signal is delivered
- **THEN** the previous handler is already back in place, so the process is
  still killable

### Requirement: Slash commands route to the same engine

`issue_comment` events SHALL parse into commands — `/review` (with `full`
forcing a full re-review), `/improve`, `/ask <q>` replying in-thread from a
task-specific answer object, `/describe` upserting a structured description,
and `/diagram` upserting a compact Mermaid change diagram rendered locally
from structured graph data — all dispatched to the same engine and provider
stack.
<!-- anchor: cli.slash -->

#### Scenario: reviewer comments /review full
- **WHEN** the comment body is `/review full`
- **THEN** a full review runs, bypassing triage and incremental scoping

#### Scenario: reviewer asks a question
- **WHEN** the comment body is `/ask <question>` and the provider returns a valid answer object
- **THEN** only the validated answer text is posted in-thread

#### Scenario: ask provider returns the wrong schema
- **WHEN** `/ask` returns object or array JSON without a valid answer
- **THEN** the raw JSON is not posted and the comment explains that no valid answer was produced

#### Scenario: reviewer comments /diagram
- **WHEN** the comment body is `/diagram`
- **THEN** typed nodes and edges are rendered into Mermaid and text views with
  stable ids, escaped labels, compact cards, and change markers on nodes

#### Scenario: provider returns diagram syntax instead of graph data
- **WHEN** the diagram provider ignores the graph contract and returns diagram
  source (C4, a Mermaid fence) with no nodes
- **THEN** that source is not posted as Mermaid and the comment explains that no
  valid diagram was produced

### Requirement: Change diagrams show structure and sequence

The change diagram SHALL summarize what the pull request changes and render two
complementary views from the same structured call: a Mermaid flowchart of the
components the change touches, and a Mermaid sequence diagram of the ordered
run-time interactions it alters. The concise summary SHALL appear above the
diagrams in the same comment, lead with the highest-impact change, keep one
change per sentence, and omit preamble, process recap, filler, and tangents.
Steps referencing unknown components SHALL be dropped, the
step count SHALL be bounded, participant and message text SHALL be escaped with
Mermaid entity codes, and the sequence view SHALL be omitted — section headings
included — when the model reports no run-time flow.
<!-- anchor: cli.diagram-sequence -->

#### Scenario: change alters a run-time flow
- **WHEN** the provider returns a change summary and ordered steps between known
  components
- **THEN** the summary renders above a `sequenceDiagram` beside the flowchart
  under `Structure` and `Sequence` headings, each view with its own text version
  and link

#### Scenario: change has no run-time flow
- **WHEN** the provider returns a change summary and no steps
- **THEN** the comment carries the summary and flowchart, with no sequence
  section and no diagram headings

#### Scenario: the same diagram printed in a terminal
- **WHEN** `lgtmaybe diagram` prints the body locally
- **THEN** the summary remains above the diagrams, each collapsible text version
  becomes a labelled section with its HTML wrapper removed, and the Mermaid
  source stays intact to paste elsewhere

### Requirement: Stale review-comment events are inert

The Action SHALL exit successfully on a `pull_request_review_comment` event
before loading review configuration, constructing a provider, or accessing
GitHub, so an obsolete workflow trigger cannot spend tokens or post output.
<!-- anchor: cli.review-comment-noop -->

#### Scenario: an old workflow delivers a review-comment event
- **WHEN** the Action receives `pull_request_review_comment`
- **THEN** it exits successfully without reading review config, calling a
  provider, or posting to GitHub

### Requirement: Conversational answers are directly actionable

Provider prompts for `/ask` answers SHALL require the response to begin with the
direct answer, omit preamble, tangents, recap, and closing pleasantries, use
numbered steps only when the work is genuinely multi-step, and end with one
concrete next action only when action remains. Purely informational answers
SHALL stop after answering instead of inventing a task for the reader.
<!-- anchor: cli.response-style -->

#### Scenario: User asks a direct question
- **WHEN** `/ask` requests information that needs no follow-up work
- **THEN** the answer leads with the result and stops without a fabricated next action

#### Scenario: Answer requires several actions
- **WHEN** an answer requires more than one bounded action
- **THEN** those actions are presented as the fewest numbered steps that still work

#### Scenario: the diff is context for a question, not a review request
- **WHEN** the diff is wrapped for `/ask`
- **THEN** it carries the same neutralised untrusted-data guard as a review diff
  but neither the review task restatement nor the review lead-in, which would ask
  for the findings JSON object from a call whose answer is prose

#### Scenario: the model answers with a machine envelope
- **WHEN** the response is entirely a JSON object or array
- **THEN** it is refused rather than posted, while prose that merely quotes braces
  or a fenced JSON example is relayed

### Requirement: Local review needs no GitHub

`lgtmaybe review` in a repo SHALL build the context from git alone: branch
diff against the resolved base (`origin/HEAD` → `origin/main` →
`origin/master` → `main` → `master`, `--base` overrides), `--working` for the
whole worktree vs the merge-base, `--uncommitted` for edits vs HEAD, with
commit subjects feeding the intent lens. Both worktree modes include untracked
files (`.gitignore` honoured) as new-file patches, since `git diff` never
reports them; branch mode reviews committed history only. Paths are resolved
against the worktree's top level, not the caller's directory.
<!-- anchor: cli.local-context -->

#### Scenario: developer reviews before pushing
- **WHEN** `lgtmaybe review --working` runs in a repo with no PR
- **THEN** findings print locally (human/json/agent format); nothing posts

#### Scenario: developer reviews a file they have not added yet
- **WHEN** `lgtmaybe review --uncommitted` runs and a new file is untracked
- **THEN** the file is reviewed, unless `.gitignore` excludes it

#### Scenario: review is started from a subdirectory
- **WHEN** `lgtmaybe review` runs from a package directory, not the repo root
- **THEN** it sees the whole worktree, with each file's head text loaded

### Requirement: Config layers merge, secrets never persist

Config SHALL merge user-level file → repo `.lgtmaybe.yml` → CLI flags (most
specific wins), and the user-level store persists only non-secret defaults —
API keys stay in the environment, never written to disk. An explicitly chosen
config path (`--config` / the Action's `config_path`) must exist and parse to
a mapping — a typo'd path fails loudly rather than silently running with
defaults; the default `.lgtmaybe.yml` probe stays lenient when absent.
<!-- anchor: cli.config -->

#### Scenario: user tries to persist a key
- **WHEN** `lgtmaybe config set` targets an API key
- **THEN** it is refused; keys are read from env/flags at run time only

### Requirement: The Action selects GitHub identity explicitly

The GitHub Action SHALL default to the built-in workflow token and perform the
public App OIDC exchange only when `github_identity: lgtmaybe` is selected,
while retaining App ID/private-key inputs as an advanced self-managed path.
<!-- anchor: cli.github-identity -->

#### Scenario: Identity input is omitted
- **WHEN** a user runs the Action without a GitHub identity input
- **THEN** the supplied or default `github_token` is used exactly as before

#### Scenario: Identity configuration conflicts
- **WHEN** public lgtmaybe identity and self-managed App credentials are both set
- **THEN** the Action fails before review execution and asks the user to choose one path

### Requirement: Branded setup remains provider-independent

Selecting a GitHub posting identity SHALL NOT change provider, model, provider
authentication, review configuration, or local CLI behavior.
<!-- anchor: cli.github-identity-provider -->

#### Scenario: User changes only identity mode
- **WHEN** an existing workflow switches from Actions identity to lgtmaybe identity
- **THEN** all provider and review inputs reach the same runtime entrypoint unchanged

### Requirement: Text boundaries are deterministic across host locales

The CLI, local git adapter, and configuration store SHALL read and write owned
text as UTF-8 on every host. External subprocess output MUST decode as UTF-8
with undecodable bytes replaced, path names MUST arrive unescaped rather than
C-quoted, and CLI stdout and stderr MUST emit safely when the inherited stream
uses a legacy Windows encoding.
<!-- anchor: cli.utf8-boundaries -->

#### Scenario: configuration contains non-Latin text
- **WHEN** a user stores and reloads non-Latin configuration values on Windows
- **THEN** the values round-trip as UTF-8 without locale-dependent corruption

#### Scenario: a clean review writes an emoji to redirected output
- **WHEN** stdout is redirected through a cp1252 text stream
- **THEN** the CLI emits the summary without raising `UnicodeEncodeError`

#### Scenario: git emits an undecodable byte
- **WHEN** the local git subprocess returns output that is not valid UTF-8
- **THEN** the command retains the decodable output and replaces only the
  malformed byte sequence

#### Scenario: a changed file has a non-ASCII name
- **WHEN** `café.py` changes and git would C-quote it as `"caf\303\251.py"`
- **THEN** the path arrives as `café.py`, so the file is reviewed like any other

### Requirement: Starter workflows enable automatic diagrams

The supplied GitHub Actions starter workflows SHALL opt in to automatic change diagrams and the dogfood workflow SHALL keep the same setting while using the faster default review preset. When enabled, automatic diagrams SHALL refresh on `opened`, `reopened`, and `synchronize` events from the full current PR context, post after the review result, and carry the head marker that proves the end-to-end run completed. When explicitly disabled, the posted review result alone SHALL be the completion watermark.
<!-- anchor: cli.starter-workflow-diagrams -->

#### Scenario: New repository adopts a supplied workflow
- **WHEN** a maintainer copies a supplied provider workflow into a repository
- **THEN** the workflow passes `auto_diagram: true` to the lgtmaybe Action

#### Scenario: Faster default is adopted
- **WHEN** the supplied workflow runs a default review
- **THEN** automatic C4 diagram generation remains enabled

#### Scenario: New push replaces an opened review
- **WHEN** a `synchronize` event replaces or follows the pull request's `opened`
  review while automatic diagrams are enabled
- **THEN** the surviving run posts or updates the change diagram

#### Scenario: A new head completes
- **WHEN** a non-partial review and required diagram both post for the current head
- **THEN** later synchronize runs may use that head as their hybrid-review base

#### Scenario: Diagram generation fails
- **WHEN** automatic diagrams are enabled and the current-head diagram cannot be generated or posted
- **THEN** the run fails without advancing completion, even if its review result already posted

### Requirement: Homepage demonstrates change diagrams

The main documentation homepage SHALL render a representative C4-style Mermaid
change diagram and link readers to the detailed change-diagram guide.
<!-- anchor: cli.docs-homepage-diagram -->

#### Scenario: Visitor opens the docs homepage
- **WHEN** a visitor opens the main documentation homepage
- **THEN** they see a rendered C4-style change-diagram example near the feature
  description and can follow its link to the full guide

### Requirement: Homepage diagram conveys change breadth

The main documentation homepage SHALL use a representative C4-style Mermaid
example that distinguishes changed and new elements and maps a change across
multiple cooperating containers.
<!-- anchor: cli.docs-homepage-diagram-breadth -->

#### Scenario: Visitor evaluates the diagram feature
- **WHEN** a visitor views the homepage change-diagram example
- **THEN** they can see changed and new elements connected across application,
  asynchronous, data, and external-service boundaries

### Requirement: Homepage overview stays concise

The main documentation homepage SHALL introduce every review category, its
trust boundaries, and its pull-request commands in no more than 400 source words
before the "Start here" section.
<!-- anchor: cli.docs-homepage-overview -->

#### Scenario: Visitor scans the homepage
- **WHEN** a visitor reads from the hero to the "Start here" section
- **THEN** they reach the commands and diagram without losing any review category

### Requirement: Action distribution major alignment

The GitHub Action SHALL default to the GHCR image whose floating major matches
the package major, and maintained workflow examples SHALL use that same Action
major.
<!-- anchor: distribution.action-major -->

#### Scenario: package major is released
- **WHEN** the package version belongs to major v1
- **THEN** the Action defaults to the v1 image and maintained workflows use
  `MattJColes/lgtmaybe@v1`

#### Scenario: a future major changes
- **WHEN** the package major changes without updating the Action image default
- **THEN** the deterministic distribution alignment test fails
