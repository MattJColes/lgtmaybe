# cli-and-local Specification

## Purpose

The Click CLI (`cli/`), local no-GitHub review of a git diff (`local/`), and
layered config (`config/`) — one engine behind `review`, `comment`, and
`action` entrypoints. The slash-command router and the change overview live in
`conversation-and-overview`.
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

`run_review` SHALL orchestrate the shared flow — completed-head read, whole-PR risk assessment, same-head and unchanged-diff no-op, incremental vs full decision, explicit earlier-finding validation, engine call, posting, and completion stamping — so `review`, `comment`, `action`, and `gitlab-ci` never duplicate review logic. Automatic synchronize runs SHALL use the hybrid incremental path, and so SHALL every `gitlab-ci` pipeline with `incremental` unset (GitLab CI has no synchronize action; the missing watermark makes the first pipeline full); explicit `incremental: false`, `gitlab-ci --no-incremental` / `LGTMAYBE_INCREMENTAL=false`, and `/review full` SHALL run a full review.
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
