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

`run_review` SHALL orchestrate the shared flow — watermark read, incremental
vs full decision, optional auto-describe, engine call, posting — so `review`,
`comment`, and `action` never duplicate review logic.
<!-- anchor: cli.run-review -->

#### Scenario: Action synchronize event
- **WHEN** the Action runs on `synchronize` with `incremental` unset
- **THEN** the same orchestrator picks incremental review automatically

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

#### Scenario: provider returns legacy C4
- **WHEN** the diagram provider ignores the graph contract and returns C4
  source together with an ASCII rendering
- **THEN** the C4 source is not posted as Mermaid and the ASCII rendering is
  shown instead

### Requirement: Finding-thread replies are answered in-thread

A `pull_request_review_comment` reply in a finding thread lgtmaybe opened SHALL
be answered in that same thread — using the finding and its surrounding diff
hunk as context, the reply treated as untrusted input — only when it is a
freshly *created* reply from a non-bot author whose thread root carries the
finding marker, and gated on `answer_replies`; every other case posts nothing so
the reviewer never answers itself.
<!-- anchor: cli.review-reply -->

#### Scenario: PR author replies in a finding thread
- **WHEN** the author replies to a lgtmaybe finding comment and `answer_replies` is on
- **THEN** lgtmaybe answers in that thread using the finding + hunk as context

#### Scenario: a bot reply arrives
- **WHEN** the review-comment author is a bot, or it is not a freshly created reply
- **THEN** nothing is posted, so the reviewer never loops on its own replies

### Requirement: Local review needs no GitHub

`lgtmaybe review` in a repo SHALL build the context from git alone: branch
diff against the resolved base (`origin/HEAD` → `origin/main` →
`origin/master` → `main` → `master`, `--base` overrides), `--working` for the
whole worktree vs the merge-base, `--uncommitted` for edits vs HEAD, with
commit subjects feeding the intent lens.
<!-- anchor: cli.local-context -->

#### Scenario: developer reviews before pushing
- **WHEN** `lgtmaybe review --working` runs in a repo with no PR
- **THEN** findings print locally (human/json/agent format); nothing posts

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

### Requirement: Text boundaries are deterministic across host locales

The CLI, local git adapter, and configuration store SHALL read and write owned
text as UTF-8 on every host. External subprocess output MUST decode as UTF-8
with undecodable bytes replaced, and CLI stdout and stderr MUST emit safely
when the inherited stream uses a legacy Windows encoding.
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
