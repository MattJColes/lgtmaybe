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

### Requirement: Review help discloses progressively

`lgtmaybe review --help` SHALL list only the everyday options (provider,
model, base, working, uncommitted, format/json, min-severity, full, config)
and end with one line counting the advanced tuning options and naming
`--help-all`, which lists every option under Common and Advanced headings.
The split is rendering only: every option parses the same either way.
<!-- anchor: cli.progressive-help -->

#### Scenario: first-time user asks for help
- **WHEN** `lgtmaybe review --help` runs
- **THEN** the output fits one screen (about 40 lines) and points at
  `--help-all` for the rest

### Requirement: One orchestrator behind every entrypoint

`run_review` SHALL orchestrate the shared flow — completed-head read, whole-PR risk assessment, same-head no-op, incremental vs full decision, explicit earlier-finding validation, engine call, posting, and completion stamping — so `review`, `comment`, `action`, and `gitlab-ci` never duplicate review logic. Automatic synchronize runs SHALL use the hybrid incremental path, and so SHALL every `gitlab-ci` pipeline with `incremental` unset (GitLab CI has no synchronize action; the missing watermark makes the first pipeline full); explicit `incremental: false`, `gitlab-ci --no-incremental` / `LGTMAYBE_INCREMENTAL=false`, and `/review full` SHALL run a full review.
<!-- anchor: cli.run-review -->

#### Scenario: Action synchronize event
- **WHEN** the Action runs on `synchronize` with `incremental` unset after a completed review
- **THEN** the same orchestrator scans the new compare diff and validates earlier open findings

#### Scenario: reviewer forces a full review
- **WHEN** `/review full` runs after a completed review
- **THEN** completion state is ignored and the entire PR is reviewed again

#### Scenario: a later GitLab CI pipeline
- **WHEN** `gitlab-ci` runs with `incremental` unset on a merge request with a completed watermark
- **THEN** only the commits since the watermark are reviewed

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
task-specific answer object, `/describe` upserting a standalone structured
description, and `/diagram` upserting the change overview (description, high
impact areas, and Mermaid diagrams rendered locally from structured graph
data) — all dispatched to the same engine and provider stack. A command SHALL
run only for a commenter the gateway confirms has write access; anyone else's
(or an unverifiable one) is ignored, exit zero, with no model call.
<!-- anchor: cli.slash -->

#### Scenario: reviewer comments /review full
- **WHEN** the comment body is `/review full`
- **THEN** a full review runs, bypassing triage and incremental scoping

#### Scenario: a drive-by commenter types /review
- **WHEN** the commenter has no write access to the repository
- **THEN** the command is ignored with a log line and nothing is posted

#### Scenario: reviewer asks a question
- **WHEN** the comment body is `/ask <question>` and the provider returns a valid answer object
- **THEN** only the validated answer text is posted in-thread

#### Scenario: ask provider returns the wrong schema
- **WHEN** `/ask` returns object or array JSON without a valid answer
- **THEN** the raw JSON is not posted and the comment explains that no valid answer was produced

#### Scenario: reviewer comments /diagram
- **WHEN** the comment body is `/diagram`
- **THEN** the change overview upserts under the diagram marker family, its
  typed nodes and edges rendered into Mermaid and text views with stable ids,
  escaped labels, compact cards, and change markers on nodes

#### Scenario: provider returns diagram syntax instead of graph data
- **WHEN** the diagram provider ignores the graph contract and returns diagram
  source (C4, a Mermaid fence) with no nodes
- **THEN** that source is not posted as Mermaid and the comment explains that no
  valid diagram was produced

### Requirement: Change diagrams show structure and sequence

The change overview's diagram call SHALL render two complementary views: a
Mermaid flowchart of the components the change touches, and a Mermaid sequence
diagram of the ordered run-time interactions it alters. A summary SHALL head the
comment — the description when one is enabled, otherwise the diagram's own —
leading with the highest-impact change, keeping one change per sentence, and
omitting preamble, process recap, filler, and tangents. Steps referencing
unknown components SHALL be dropped, the step count SHALL be bounded,
participant and message text SHALL be escaped with Mermaid entity codes, and the
sequence view SHALL be omitted when the model reports no run-time flow.
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
  section

#### Scenario: the same overview printed in a terminal
- **WHEN** `lgtmaybe diagram` prints the body locally
- **THEN** the summary remains above the diagrams, each collapsible text version
  becomes a labelled section with its HTML wrapper removed, and the Mermaid
  source stays intact to paste elsewhere

### Requirement: The change overview calls out high impact areas

The change overview SHALL carry a High Impact Areas section from its own
structured call, naming the changes whose blast radius reaches beyond the diff —
infrastructure, security posture, availability, data migrations, backups and
recovery, compatibility, observability, dependencies, cost, and compliance.
Deterministic path signals SHALL ground that call as untrusted data and SHALL
floor its result, so an area implicated by a changed path is named even when the
model reports nothing for it. A failed or unparseable call SHALL still render
those signals rather than dropping the section, an empty result SHALL name what
was checked, model-reported paths absent from the change SHALL be dropped, and
the section SHALL never fail the run.
<!-- anchor: cli.overview-high-impact -->

#### Scenario: model reports nothing for a changed sensitive path
- **WHEN** the change edits an infrastructure path and the model returns no area for it
- **THEN** the section still names that area and path, marked as not assessed

#### Scenario: high-impact call fails
- **WHEN** the provider raises or returns output that cannot be parsed
- **THEN** the section renders the path signals with a notice that the model
  assessment is unavailable, and the rest of the overview still posts

#### Scenario: nothing qualifies
- **WHEN** the model returns no areas and no path signals matched
- **THEN** the section states that none were detected and lists the areas checked

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
against the worktree's top level, not the caller's directory. An empty diff
SHALL make no model call and print no LGTM; it exits non-zero, naming
`--base`, when no default base existed and the comparison fell back to HEAD.
<!-- anchor: cli.local-context -->

#### Scenario: developer reviews before pushing
- **WHEN** `lgtmaybe review --working` runs in a repo with no PR
- **THEN** findings print locally (human/json/agent/sarif format); nothing posts

#### Scenario: developer reviews a file they have not added yet
- **WHEN** `lgtmaybe review --uncommitted` runs and a new file is untracked
- **THEN** the file is reviewed, unless `.gitignore` excludes it

#### Scenario: review is started from a subdirectory
- **WHEN** `lgtmaybe review` runs from a package directory, not the repo root
- **THEN** it sees the whole worktree, with each file's head text loaded

### Requirement: A local review can gate on severity

`lgtmaybe review --fail-on <severity>` SHALL exit with status 3
(`FAIL_ON_EXIT_CODE`) after printing its findings when any finding is at or
above that severity, so a pre-commit / pre-push hook or script can block on
findings. Status 3 is distinct from 1 (the review could not run) and 2 (a usage
error). Only the explicit flag gates a local run: a `fail_on` from config is
the Action's merge gate and never changes a local exit status.
<!-- anchor: cli.local-fail-on -->

#### Scenario: a hook blocks a push on a high finding
- **WHEN** the `lgtmaybe-pre-push` hook from `.pre-commit-hooks.yaml` runs
  `lgtmaybe review --fail-on high` and finds a `high` or `critical` issue
- **THEN** the findings print on stdout and the run exits 3, stopping the push

#### Scenario: the repo config sets fail_on
- **WHEN** `.lgtmaybe.yml` sets `fail_on` and `review` runs without the flag
- **THEN** the local run exits 0 whatever it finds

### Requirement: Diagnostics never corrupt machine output

stdout SHALL carry only the review's deliverable. The opt-in profile table is a
human artefact, so it SHALL move to stderr whenever the findings themselves are
machine-readable — otherwise a run asking for both emits neither cleanly. The
profile SHALL also be available as data, written to a path rather than a stream
so it collides with no output format, carrying a schema version so a consumer
can pin against it, and reporting an unknown count as null rather than a
sentinel a parser must string-match. A diagnostic that cannot be written SHALL
NOT fail a review that produced findings.
<!-- anchor: cli.profile-output -->

#### Scenario: machine-readable findings with the profile enabled
- **WHEN** a review is asked for JSON findings and the profile together
- **THEN** stdout parses as the findings alone, and the table is on stderr

#### Scenario: a consumer wants the profile as data
- **WHEN** a profile path is given
- **THEN** the file holds a versioned payload whose unknown counts are null

#### Scenario: the profile path cannot be written
- **WHEN** writing the profile file fails
- **THEN** the review still reports its findings

### Requirement: Coding agents review over MCP

`lgtmaybe mcp` SHALL serve the local review to Model Context Protocol clients
over stdio, through the same engine path (`run_local_review`) and config layers
as `lgtmaybe review`, re-read on every call. Two read-only tools take a diff
`mode` (branch, working, uncommitted) plus optional `base`, `min_severity` and
`preset`: `review_changes` returns the `json` format's findings with the summary
line and an incomplete flag, `review_instructions` the `agent` format. stdout
SHALL carry only the protocol. A failed review SHALL be a tool error naming the
reason, never an empty result or a dead server. The SDK is the optional `mcp`
extra, imported only by the command, which names the extra when it is missing.
The server SHALL stop on SIGTERM rather than take the CLI's wind-down.
<!-- anchor: cli.mcp-server -->

#### Scenario: an agent asks for a review of its edits
- **WHEN** a client calls `review_changes` with `mode: working`
- **THEN** it receives the findings as data, and stdout held only JSON-RPC

#### Scenario: the server runs outside a repository
- **WHEN** a tool call cannot build the git context
- **THEN** the call returns a tool error with the reason and the server serves on

#### Scenario: the extra is not installed
- **WHEN** `lgtmaybe mcp` runs without the MCP SDK
- **THEN** it exits non-zero with the `pip install 'lgtmaybe[mcp]'` hint

### Requirement: Local findings export as SARIF

`lgtmaybe review --format sarif` SHALL print a deterministic SARIF 2.1.0 log of
one run, for code-scanning upload. Each lens id is one rule; each finding's
level folds the Severity scale (critical/high error, medium warning, low/info
note), and its `partialFingerprints` carry the versioned finding fingerprint
and identity the forge adapters embed. A result SHALL carry a line region only
when the line is real in the checked-out file: an unanchored or LEFT-side
finding names the file alone. A suggestion stays in the property bag, never a
`fixes` entry, because free text replaces no region. A run that did not
complete every lens reports `executionSuccessful: false`.
<!-- anchor: cli.sarif-output -->

#### Scenario: findings uploaded to code scanning
- **WHEN** a review runs with `--format sarif`
- **THEN** stdout parses as the SARIF log alone, and a re-run yields the same bytes

#### Scenario: a lens failed mid-review
- **WHEN** the summary carries the incomplete marker
- **THEN** the invocation is unsuccessful and the notice goes to stderr

### Requirement: Config layers merge, secrets never persist

Config SHALL merge user-level file → repo `.lgtmaybe.yml` → CLI flags (most
specific wins), and the user-level store persists only non-secret defaults —
API keys stay in the environment, never written to disk. An explicitly chosen
config path (`--config` / the Action's `config_path`) must exist and parse to
a mapping — a typo'd path fails loudly rather than silently running with
defaults; the default `.lgtmaybe.yml` probe stays lenient when absent. There
is no built-in provider or model: when no layer sets both, loading fails
before any diff or model work with a two-line pointer phrased for the
entrypoint (`config init` locally, the `provider`/`model` inputs in the
Action, flags or `.lgtmaybe.yml` for `gitlab-ci` / `comment`).
<!-- anchor: cli.config -->

#### Scenario: user tries to persist a key
- **WHEN** `lgtmaybe config set` targets an API key
- **THEN** it is refused; keys are read from env/flags at run time only

#### Scenario: nothing configured on a fresh install
- **WHEN** `lgtmaybe review` runs with no flag, repo file, or user config
  naming a provider and model
- **THEN** it exits non-zero at once, pointing at `lgtmaybe config init` or
  `--provider`/`--model`, and never guesses a model

### Requirement: First-run setup detects, then verifies

`lgtmaybe config init` SHALL detect provider API keys, cheaply visible ambient
cloud credentials, and a running ollama (with its pulled models) before
asking anything, offer the strongest detection as the default, validate the
provider against the enum, and suggest a current model from a table kept in
step with the how-to guides. Unless `--no-verify` is given, it SHALL make one
tiny completion through the review's own credential resolver and provider
factory before writing, so a bad key or model fails during setup with the
same "how to auth" message a review would show. Only provider, model and
api_base are written; keys stay in the environment.
<!-- anchor: cli.config-init -->

#### Scenario: a machine with a running ollama
- **WHEN** `config init` runs and ollama answers `/api/tags`
- **THEN** two Enter presses confirm ollama and a pulled model, the model is
  checked, and the file path and `lgtmaybe review` are printed

#### Scenario: a wrong API key
- **WHEN** the verification call fails
- **THEN** init exits non-zero with the provider's error and writes nothing

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

### Requirement: Starter workflows enable the automatic change overview

The supplied GitHub Actions starter workflows SHALL rely on the automatic change overview being enabled by default, and the dogfood workflow SHALL keep the same setting while using the faster default review preset. When enabled, the overview SHALL refresh on `opened`, `reopened`, and `synchronize` events from the full current PR context (unless the PR already diagrams the change, below), post after the review result, and carry the head marker that proves the end-to-end run completed. When explicitly disabled, the posted review result alone SHALL be the completion watermark.
<!-- anchor: cli.starter-workflow-diagrams -->

#### Scenario: New repository adopts a supplied workflow
- **WHEN** a maintainer copies a supplied provider workflow into a repository
- **THEN** the workflow sets no `auto_diagram` input, since the default enables it

#### Scenario: Faster default is adopted
- **WHEN** the supplied workflow runs a default review
- **THEN** the automatic change overview remains enabled

#### Scenario: New push replaces an opened review
- **WHEN** a `synchronize` event replaces or follows the pull request's `opened`
  review while the automatic overview is enabled
- **THEN** the surviving run posts or updates the change overview

#### Scenario: A new head completes
- **WHEN** a non-partial review and required diagram both post for the current head
- **THEN** later synchronize runs may use that head as their hybrid-review base

#### Scenario: Diagram generation fails
- **WHEN** the automatic overview is enabled and the current-head diagram cannot be generated or posted
- **THEN** the run fails without advancing completion, even if its review result already posted

### Requirement: The automatic overview defers to an existing one

The Action SHALL NOT post its automatic change overview when the pull request already diagrams the change: a fenced Mermaid block (the diagram kind every forge renders) in the PR description or in a conversation comment it did not post itself. Its own earlier overview (this provider/model's diagram marker) SHALL keep the slot and keep refreshing, and another lgtmaybe setup's overview SHALL count as existing. A skipped overview SHALL be named in the review summary with the `/diagram` command that posts one, and the posted review result alone SHALL be the completion watermark for that run. A failed comment listing SHALL still check the description and otherwise post as usual. Slash commands SHALL never consult the check.
<!-- anchor: cli.existing-overview -->

#### Scenario: another tool already posted a walkthrough diagram
- **WHEN** an `opened` or `synchronize` run finds a Mermaid fence in another author's PR comment
- **THEN** the review posts without the overview and its summary names that comment and `/diagram`

#### Scenario: lgtmaybe posted the overview earlier
- **WHEN** the conversation carries this setup's own diagram marker beside another tool's diagram
- **THEN** the overview refreshes as usual

#### Scenario: a reviewer asks for the overview anyway
- **WHEN** the comment body is `/diagram` on a PR that already carries a diagram
- **THEN** the overview upserts as usual

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
- **WHEN** the package version belongs to major v2
- **THEN** the Action defaults to the v2 image and maintained workflows use
  `MattJColes/lgtmaybe@v2`

#### Scenario: a future major changes
- **WHEN** the package major changes without updating the Action image default
- **THEN** the deterministic distribution alignment test fails
