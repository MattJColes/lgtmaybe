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
forcing a full re-review), `/improve`, `/ask <q>` replying in-thread,
`/describe` upserting a structured description, and `/diagram` upserting a
C4-style change diagram — all dispatched to the same engine and provider stack.
<!-- anchor: cli.slash -->

#### Scenario: reviewer comments /review full
- **WHEN** the comment body is `/review full`
- **THEN** a full review runs, bypassing triage and incremental scoping

#### Scenario: reviewer comments /diagram
- **WHEN** the comment body is `/diagram`
- **THEN** a C4-style change diagram is upserted as its own comment

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
API keys stay in the environment, never written to disk.
<!-- anchor: cli.config -->

#### Scenario: user tries to persist a key
- **WHEN** `lgtmaybe config set` targets an API key
- **THEN** it is refused; keys are read from env/flags at run time only
