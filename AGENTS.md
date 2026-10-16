# AGENTS.md

Guidance for agents working in **lgtmaybe**, a provider-agnostic PR reviewer.
Read it before writing code. It records decisions that are **made, not
options**: what you must not break, and where to look. For how a feature
behaves, follow its pointer (`docs/explanation/`, `ARCHITECTURE.md`,
`openspec/specs/`, or the module docstring).

## What This Is

A PR reviewer that posts inline review comments and a summary. The user picks
the LLM backend with `--provider`, puts a key in GitHub secrets (or wires
OIDC/WIF for cloud) and gets a review. One core, four distribution variants.

**The wedge:** first-class **Bedrock, Vertex and Azure with keyless OIDC/WIF**.
Seven hosted providers plus local ollama, one flag, no keys in secrets for
cloud. We win on auth and simplicity. `openai-compatible` is the escape hatch
for anything speaking the OpenAI `/v1` format (bring `--api-base`; key
optional), so the provider list is never a cage.

**CI matrix:** Ubuntu and Windows on Python 3.11, the minimum supported version.
Windows uses locale-default encoding (`PYTHONWARNDEFAULTENCODING`, and
`EncodingWarning` is an error), so code and tests must never rely on a UTF-8
default: pass `encoding=` explicitly. CI sets `core.autocrlf input` before
checkout.

## Non-Negotiables

- **TDD, always: red, green, refactor.** Write the acceptance test from the
  task's stated inputs and outputs first, watch it fail, write the minimum code
  to pass, then refactor. CI rejects a PR whose diff adds code without a test.
  Every task states its inputs, outputs and acceptance test; that test *is* the
  red step, so start there.
- **Structured output only.** The model returns JSON (`severity`, `file`,
  `line`, `body`, `suggestion`). Never parse prose.
- **Fork safety.** Trigger on `pull_request_target` so the review has secrets,
  but **never check out or execute PR code**. The diff comes via the API. The
  one sanctioned clone is the **base** branch (`SupportsBaseCheckout`,
  `github/checkout.py`), shallow-cloned for ast-grep: trusted code, parsed and
  never executed. Treat all diff content as untrusted input.
- **No required static cloud keys.** Bedrock and Vertex use ambient AWS / GCP
  creds; Azure uses a supplied `AZURE_API_KEY`, else ambient Entra creds via
  GitHub OIDC. Never require a service-account JSON or a static AWS key.
- **Errors surface to the user; never swallow them.** A failed review posts a
  short "review failed" comment and the CLI exits non-zero (`ClickException`).

## Key Decisions (Do Not Relitigate)

- **Language:** Python. **Licence:** MIT (`LICENSE`).
- **Provider spine:** [litellm], one `completion()` call for every provider,
  with a thin wrapper adding retries and fallback.
- **Finding prose is for an engineer new to the codebase:**
  `prompt.PLAIN_LANGUAGE` (plain words, short sentences, terms explained once)
  rides the shared review contract ("why it matters", a two-to-four sentence
  `body`) and the describe, high-impact and diagram prompts.
- **Posting:** the REST review API, batched inline comments plus one summary.
- **The summary names the model, never a cost.** lgtmaybe does not compute
  cost.

## Auth Model (Chain of Responsibility)

| Provider | Auth |
|---|---|
| openai / openrouter / anthropic | API key from `secrets.*` / env / `--api-key` |
| zai (GLM / Zhipu AI) | API key (`ZAI_API_KEY` / `--api-key`); litellm `zai/` route, which lacks `response_format`, so the schema goes as a forced tool call (`_honour_route_schema_support`). Optional `--api-base` / `ZAI_API_BASE` |
| bedrock | ambient AWS creds (GitHub OIDC role, or local `~/.aws`); IAM `bedrock:InvokeModel*` only |
| vertex | ambient GCP creds (WIF, or local ADC) |
| azure | needs the resource endpoint (`--api-base` / `AZURE_API_BASE`); `AZURE_API_KEY` / `--api-key` when supplied, else ambient Entra creds (OIDC via `azure/login`, or local `az login` / managed identity) |
| ollama | none, just an `api_base`; fully local, zero cost |
| openai-compatible | requires the endpoint (`--api-base` / `OPENAI_COMPATIBLE_API_BASE`); key **optional** (`--api-key` / `OPENAI_COMPATIBLE_API_KEY`, else a placeholder for keyless local servers). litellm `openai/` route to a custom base |

Resolver order: chosen provider → its API key when supported and supplied →
else ambient cloud creds if that is its native mode → ollama needs neither →
openai-compatible needs an `api_base` (key optional) → else **fail with a clear
"how to auth this provider" message**. Detail: `docs/explanation/auth-model.md`.

## Architecture: Ports and Adapters

Detail: `docs/explanation/architecture.md` and `ARCHITECTURE.md`.

- **`core/ports.py` is frozen.** Everything downstream codes against it.
  litellm and the forge gateways are adapters; `cli.build_review_context` wires
  the real ones; `python -m lgtmaybe` (the Docker ENTRYPOINT) is the Click CLI.
  Structured logging serves CI debugging.
- **The engine is a pipeline** of composable stages: `fetch → compress → prompt
  → parse → re-anchor → merge/dedupe → reflect → arbitrate → filter → post`,
  fanning out one concurrent model call per lens.
- **Provider choice:** strategy plus factory (`--provider` picks the strategy, a
  factory builds the `ProviderClient`). **Credentials:** chain of
  responsibility. **Dependency injection:** ports go into the engine, so fakes
  and dry-run drop in.
- **Deliberately skipped** (do not add without a written reason): repository
  pattern, event bus, plugin framework.
- **Line anchoring (do not trust model arithmetic):** every finding carries a
  verbatim `anchor`; `engine._snap_findings` re-anchors `line` to the matching
  changed line. An unmatched anchor marks it `anchored=False` and the adapter
  **demotes it to the review body** (`core/comment.render_demoted`): a
  wrong-line comment breaks trust faster than an imprecise one. No anchor:
  trust the model's line.

## Forges (Code Hosts)

`Forge` (`core/forge.py`: github / gitlab / gitea / azure-devops) is a second
axis, orthogonal to the LLM `Provider`. **Never overload "provider" for a code
host.** Host differences: `docs/explanation/architecture.md` and
`docs/how-to/review-on-*.md`.

- **The engine never sees a gateway.** It takes a `PRContext` and returns
  findings (`local/` builds one from `git` with no forge). Host-neutral code
  belongs in `core/`, never an adapter: diff parsing and the skip filter
  (`core/diff.py`), fingerprints (`core/findings.py`), comment Markdown
  (`core/comment.py`), diff rendering (`core/diffbuild.py`). **An adapter that
  reimplements any of those is a bug.**
- **`ReviewGateway` (`core/ports.py`) is three methods** (`get_pr_context`,
  `post_review`, `post_issue_comment`). Anything richer is an
  optional `Supports*` protocol the CLI probes for and skips when absent.
  Declare only what the host can serve: a capability that cannot work is worse
  than none.
- `core/forge.parse_pr_url` gives a `PRLocator` (forge, host, repo, number);
  `cli._GATEWAY_BUILDERS` maps forge to adapter. Host is carried because
  self-hosted GitLab/Gitea is the norm.
- **GitHub** is the complete adapter. **Gitea** (`gitea/gateway.py`) lacks
  incremental review and thread resolution (its API cannot serve them); its
  summary is an issue comment because a submitted Gitea review is immutable.
- **GitLab** (`gitlab/gateway.py`): one discussion per finding, an upserted
  summary note. Thread resolution is plain REST, gated on the validated
  allowlist and scoped to the increment. Incremental needs a single-parent chain
  back to the watermark, else full review. Read the watermark only from the
  token user's own note; an unmarked post keeps the old one. 👎 feedback reads
  `thumbsdown` award emoji only from Developer+ members (others cannot steer
  it).
- **Azure DevOps** (`azure_devops/gateway.py`, Services, api-version 7.1):
  builds the diff from both sides with `core/diffbuild.py`. 401/403 fail loudly;
  a file that keeps failing on 429/5xx degrades to binary and is named. De-dupe
  trusts only live, non-`fixed` threads whose root comment our identity
  (`authenticatedUser.id`) wrote, and **none when the identity is unknown**.
  Upserts edit only our own marked threads (identity unknown: try each and let
  the host's 403 decide). Resolve-on-fix is gated on `mark_reviewed(head)`, so a
  partial run or failure notice resolves nothing. Incremental, labels, checks
  and feedback are unbuilt and **unclaimed**. Token: `AZURE_DEVOPS_TOKEN` else
  `SYSTEM_ACCESSTOKEN`.
- **Entrypoints:** GitHub and Gitea Actions share `action` (only
  `GITHUB_SERVER_URL` differs, read by `cli._change_url`). GitLab CI uses
  `gitlab-ci` (reads `CI_*`; unset `incremental` resolves to on,
  `cli.resolve_ci_incremental`). Azure Pipelines uses `azure-pipelines`
  (`SYSTEM_*`/`BUILD_*`).

## Posting and Re-runs

- **Provenance badge** (`core/comment.finding_badge`): `**[HIGH · security ·
  80%] Title**`. Visible prose only, **never part of the hidden ids**.
- **Hidden summary markers:** watermark (`<!-- lgtmaybe-reviewed:<sha> -->`),
  risk level, and `<!-- lgtmaybe-lenses:… -->` (`engine._lenses_marker`; a
  failed lens is left out) so a gate can require the lens set it expects. A
  same-commit update edits in place; a review at a new commit posts a fresh
  summary, preserving history.
- **Incremental posting (GitHub):** new findings ride the new commit's one
  native review, de-duped against our own posted ids; a 422 falls back to one comment
  each. A same-commit re-run posts individual review comments, because the
  review-update endpoint cannot add inline comments.
- **Two hidden ids per comment:** `finding_fingerprint(path, title)` keys the
  user-facing channels (`ignore_fingerprints`, 👎 feedback);
  `finding_identity(finding)` hashes path, category, side and anchor, so it
  holds no model prose (`core/findings.py`). Dedupe and
  resolve-on-fix **match on either**. Never key on the fingerprint alone: a
  reworded title would be re-posted.
- **Resolve-on-fix** (`resolve_fixed`, default on): only when the finding is
  gone **and** the thread is outdated. GitHub uses GraphQL, best-effort; it
  never fails the review.
- **Clean review:** `👍 LGTM!` as a comment, never approval state, naming the
  model. A bad `summary_template` falls back to the built-in line.
- **Stacked PRs (GitHub only):** the gateway fills `PRContext.stack` from the
  native `stack` object, else a same-repository base-branch chain (capped,
  best-effort, never fails a review); the engine sends it redacted as a STACK
  block on the shared prefix, zero bytes outside a stack. A
  `<!-- lgtmaybe-diff:… -->` fingerprint (`core/diff.diff_fingerprint`) rides
  beside the watermark, so a rebase that leaves the diff unchanged is skipped.
  `edited` with `changes.base` is a full review; any other `edited` reviews
  nothing. Detail: `docs/how-to/review-stacked-pull-requests.md`.

## Security Rules

Two separate concerns: our own hardening, so a malicious PR cannot subvert us,
and what we look for in the PR.

- Diff content, file paths, PR text and commit messages are untrusted
  everywhere they flow; on a fork PR they are attacker-chosen.
- **Injection defence** (`engine/injection.py`): each untrusted block gets its
  own marker family (`_FAMILIES`: DIFF, INTENT, HINTS, CONTEXT, SPEC,
  VALIDATION, HIDDEN, SIGNALS, STACK) via its `wrap_*` helper, and forged
  markers of every family are neutralised in every block. Other untrusted text
  goes through `injection.neutralise`. A new untrusted block gets its own family
  and a test.
- **Redaction before egress** (`engine/redact.py`): cloud and SaaS keys, PEM
  blocks, quoted passwords, `Authorization` headers, connection strings.
  Everything sent to a model is redacted, not only the diff.
- **Schema enforcement:** `extra=forbid` rejects drifted or injected fields.
- **Trusted config only:** custom lenses, directory rules and the project
  profile enter the prompt, so they **never come from PR-author content**. On
  `pull_request_target`, config and workspace files come from the base
  (`Path.cwd()`), **never `github.get_file_contents`**, which reads the PR head.
- **No user code ever runs.** Finding rules are declarative. Static-analysis
  tools run sandboxed (scrubbed env, no network, hard timeout, no checkout);
  semgrep uses local rules only, **never `--config auto`**.
- **API keys are never persisted** to user config; they stay in the
  environment.
- **What we look for** (the OWASP-aligned checklist and every other scan
  category) lives in `engine/prompt.py`; catalogue in
  `docs/explanation/what-gets-reviewed.md`.
- **Tests are mandatory.** Touching redaction, injection, the prompt or the skip
  filter means extending `tests/engine/` (`test_redact.py`, `test_injection.py`,
  `test_prompt.py`, `test_parse.py`, `test_engine.py`) and
  `tests/core/test_diff.py`. Every scan category is asserted in
  `tests/engine/test_prompt.py`; extend those tests when the checklist changes.
  Guarded mechanics: one category-matched worked example per prompt with a real
  hunk header, the `line`/`side` arithmetic in the contract, and the injection
  wrapper's restatement matching `{"findings": []}`.

## Engine Features (Pointers)

One line per feature: where it lives, its switch and default, and the rule you
must not break. Config fields are on `ReviewConfig` (full list:
`docs/reference/config.md`); behaviour is in `docs/explanation/` and the module
docstrings.

**Input and batching**

- **Guards:** `is_reviewable` skips generated/binary files (lockfiles, minified,
  vendored); `engine.passes_path_filters` then applies `include_paths` /
  `exclude_paths` (exclude wins; `**/` also matches at the root); the file cap
  posts a "reviewed top N of M" notice.
- **Context expansion** (`compress.expand_hunks`, `context_lines` default 20,
  `0` off): head text comes via the API, never a checkout. Positions stay bound
  to the **real** diff: a finding on a context-only line is dropped, never
  mis-posted.
- **Recursive walk** (`compress.split_patch_into_hunks`, `recursive` default
  on): an over-budget file is reviewed hunk by hunk. A defaulted
  `max_input_tokens` is fitted to the model (`engine._fit_input_budget`); a
  configured one is left alone. One `cl100k_base` encoder serves every model; a
  per-model lookup bought nothing.
- **Incremental review** (`incremental` default None = auto: on for
  `synchronize` and `gitlab-ci`): reviews only commits since the watermark.
  `mark_reviewed` stamps it on success only: **a failure never moves it**. Falls
  back to full on no watermark, force-push, same head or API failure.
  Resolve-on-fix is scoped to the increment (`set_incremental_scope`), so an
  un-re-reviewed finding is never resolved; LEFT-side findings are dropped
  (their lines are relative to the last-reviewed head, not the base).
  `/review full` forces a full review.
- **Triage** (`engine/triage.py`, `triage_model`, default off):
  `triage.always_escalate` is a deterministic security floor; any triage
  failure reviews everything; skips are named in the summary; `/review full`
  bypasses triage and incremental scoping. All model slots share one provider
  and credentials.
- **Static analysis** (`engine/static_analysis.py`, `static_analysis.enabled`,
  default **on**, `lgtmaybe[static-analysis]` extra): every tool defaults to
  `hint` mode (`_DEFAULT_MODE`), an untrusted HINTS block the model confirms,
  contextualises or discards, so nothing posts without the model. `finding`
  mode (skip the model, post directly) is a per-tool opt-in via
  `static_analysis.tool_mode`; the trade-off is that a hint can be discarded.
  Hints on files no batch holds (lockfile advisories) ride the first batch's
  security lens (else correctness, else named in the summary) on its own
  uncached block, **never the shared prefix**; a confirmed finding there is
  demoted to the review body and exempt from the unanchored floor, and
  reflection gets the matching hints as its evidence (`reflect._scan_hint_block`,
  its own block, zero bytes when no such finding exists).
  `_scanner_covers` drops a lens's secret/advisory ask only for a
  `finding`-mode scanner. **Tool output is always redacted and never posted
  verbatim.** A missing tool is skipped, so a minimal install runs nothing.
  Tests stub the engine's runner (`tests/conftest.py`) so installed linters
  never leak into prompts.

**Lenses**

- **Presets** (`preset`): `fast` (default) covers all ten everyday categories in
  five calls (`prompt.FAST_GROUPS`) on **every** provider: worker count decides
  overlap, never call count. `full` runs one per category; `categories`
  overrides both.
- **Concurrency:** one global `ThreadPoolExecutor` (`max_concurrency`, auto 6),
  then `engine._dedupe`. `max_review_seconds` (default 3600, 0 off) and
  SIGINT/SIGTERM (`engine.request_interrupt`) post partial results with a
  notice, **never a silent LGTM**. Only the CLI entrypoint installs the signal
  handler (`cli.graceful_interrupt`), **never library import**; it restores the
  previous handler as it fires and is a no-op off the main thread. `--profile`
  prints timings (`engine/profiling.py`).
- **Custom lenses** (`extra_lenses`, `lens_paths`): built like built-in lenses
  (`engine._build_lenses`). Trusted config only.
- **Intent lens:** intent is sent **only on the intent call**; none stated skips
  it.
- **Spec lens** (`engine/specs.py`, default on, detection-gated; `--spec`):
  at most `MAX_SELECTED` = 2 specs, only on the spec call. Spec files the PR
  changes are read from head text, the rest from the base workspace. A
  requirement delivered in a file the batch can't see is "not shown, not
  undelivered". A matched spec that did not fit is named in the summary, never
  skipped silently. Reflection's
  gap carve-out must name spec mismatches, or the auditor prunes them as
  cross-file absence claims.
- **Not-shown manifest** (`engine.files_not_visible`): nothing hidden adds zero
  bytes; the cap `_MAX_LISTED_NOT_VISIBLE` is applied in one place.
- **Ponytail** (`info`/`medium`): "should this exist at all?". It **never
  collapses an existing port, layer or interface boundary**. A `shortcut:`
  comment is a recorded decision; that rule binds the advisory lenses only,
  never security or correctness.
- **Architecture** (ceiling `medium`, `severity.CATEGORY_SEVERITY_CEILING`):
  each finding opens with a change scenario; a principle's name is never the
  justification. No new abstraction without a visible second variant or an
  existing boundary; layers come from the repo's own paths, never invented.
  Reflection protects it as a gap finding only when both ends of the
  relationship are shown. Its own `fast` call, not a code-health member.
  Lineage: `ATTRIBUTION.md`.
- **Opposing advice is arbitrated, never posted together.** Lens prompts **never
  name each other** (it would couple them and lock out custom lenses). The
  shared never-cut rule bars any lens from recommending removal of
  trust-boundary validation, data-loss error handling, security controls or
  accessibility, and a shorter replacement keeps every edge case. Each lens
  keeps its own evidence bar (complexity extracts duplication only for uniform
  callers; `pack:design` spares port implementations).
  - `PRECEDENCE` (`engine/conflicts.py`): correctness → security → performance
    → tests → architecture → complexity → ponytail (Ponytail 5's order, design
    slotted above readability). It is rendered into the reflection prompt so
    both referees rank alike.
  - `OPPOSED`: ponytail against correctness, security and architecture.
  - Reflection keeps the higher-ranked of two opposite recommendations only
    when both lenses are on the ladder; otherwise it keeps both.
  - `conflicts.resolve_conflicts`, the deterministic backstop after reflection
    (holds under `--no-reflect`), drops a finding only for an `OPPOSED` pair
    within `NEAR_LINES` of a higher-ranked one, and logs it.
  - Only declared pairs are arbitrated: never guess at group ids, unattributed,
    custom, pack or scan findings.
- **Directory rules** (`engine/directory.py`, YAML-only): join the per-batch
  prefix, not a new message, so `build_shared_preamble` stays byte-identical.
  No `BUGBOT.md` walk-up.
- **Project profile** (`project`, YAML-only): `stage` moves **defaults only**
  (`ReviewConfig._apply_project_stage`), so configured values win. `prototype`
  raises the `min_severity` default to `medium`. `regulated` adds a `medium`
  risk floor, never `high` (that would turn any other high reason `critical`).
  Unset is byte-identical. **Never read from AGENTS.md / CLAUDE.md / SOUL.md:**
  agent instructions are noise to a reviewer and overflow the context budget.
- **Mid-review retrieval** (`mid_review_retrieval`, default off): one hop; the
  fetched text goes on the lens's uncached block, **never the shared prefix**;
  it can only add findings; off is a zero-byte prompt change. Off by default
  because the worst case is one extra call per (batch, lens) and the recall win
  is unmeasured.

**Calls and filtering**

- **Prompt caching** (`prompt.build_shared_preamble`): one prompt shape, **no
  opt-out**. Keep the shared prefix (preamble, wrapped diff, hints)
  lens-independent and byte-identical; lens text goes in the final uncached
  block.
- **Truncation ladder** (`engine._remedy_oversized`): two detectors (adapter
  `ProviderTruncated`, and the parser catching an unterminated body) feed one
  ladder; `ProviderInputTooLarge` takes the same split. **Do not re-split
  them:** which fires is a fact about the route, not the failure. The rung
  depends on the cause, on the user's model: `_review_split` when the
  **answer** outgrew the ceiling; `_retry_lower_effort` when the **thinking**
  did (one graded step down, then one call with reasoning off). **Escalation
  is last:** only after those fail does `engine._escalate_model` re-run once on
  `fallback_model`, per batch never per split piece, because a new model
  re-sends the same request at the same ceiling.
  - Lens calls carry `defer_truncation`, so the adapter hands truncation back
    instead of falling back beneath the engine; it still rescues every other
    failure itself.
  - The parse-path notice names no `max_tokens` ceiling, because nothing
    measured one.
  - `--fallback-model` / the Action input win over `ReviewConfig.fallback_model`.
  - The answering model is disclosed (`engine._note_answering_model`).
  - **Do not route on diff size:** truncation does not track size, so a size
    threshold spends the strong model on diffs that were already fine.
- **Self-talk** (`engine/selftalk.py`, `_stamp_and_bound`): a finding is
  dropped only when its prose carries **both** halves of a self-note (a broken
  attempt plus the required shape); either alone is ordinary prose, so do not
  widen it. Drops are counted in the summary, never silent.
- **Reflection** (`engine/reflect.py`, `--no-reflect`): an unparseable verdict
  keeps everything (**never silently drop a real finding**). `min_confidence`
  default 0 (off); unscored findings survive. It drops cross-file false
  positives but carves out gap findings (performance, complexity, intent,
  spec). The test/doc carve-out is conditional: a "no test/doc" finding is
  protected only when that file is shown, because a test in an untouched file
  is not a missing test.
- **Codebase humility (prompt rule):** the diff is a slice. Hedge cross-file
  absence claims and lower their severity; never assume what an unshown
  constant or collection *contains*.
- **Determinism:** `temperature` default `0.0`; `timeout` None is
  provider-aware.
- **Finding rules** (`engine/rules.py`): declarative `drop` / `set_severity`
  keyed on the engine-stamped `category`. Never a code hook.
- **Risk of change** (`engine/risk.py`, default on, no model call): assessed on
  the **full** context before incremental scoping, so every surface agrees.
  A file it cannot measure is listed as unassessed, and a `low` verdict always
  carries that caveat; any other failure is "risk unavailable" with
  no marker. **Never approval state.** Detail:
  `docs/explanation/risk-of-change.md`.
- **Labels** (`engine/labels.py`, `pr_labels`, default off): touch only
  lgtmaybe's own label families.

**Comments beyond the review**

- **Slash commands** (`comment`): `/review`, `/improve`, `/ask`
  (replies via `post_issue_comment`), `/describe`
  (`engine/describe.py`, upsert), `/diagram`.
- **Change overview** (`engine/overview.py`, `auto_diagram` default on): one
  upserted comment with its own marker family. Description (`auto_describe`,
  default on) and High Impact Areas (`engine/high_impact.py`, `high_impact`,
  default on) are best-effort: a failure shows a visible "unavailable" line,
  never a silent gap. The diagram call (`engine/diagram.py`) propagates failure:
  it is a required completion step and must not stamp the head complete.
  `auto_diagram` is the one switch for the whole comment. Path signals are
  untrusted hints and a floor; High Impact drops model-chosen paths not in
  `changed_files`. `describe.structured_call` / `structured_comment` is the
  shared one-call scaffold; `describe.markdown_text` escapes every section's
  model prose. Model-authored Mermaid never reaches a fence; sequence labels are
  escaped with Mermaid entity codes. **No D2:** GitHub does not render it.
  `should_auto_diagram` gates the automatic overview to
  opened/reopened/synchronize; `existing_overview_note` stands it down when the
  PR already diagrams the change, and the review summary then names the skip and
  `/diagram`. Slash commands never consult it. With every section and risk off,
  the body is byte-identical to `build_diagram`.

## Local CLI

- `lgtmaybe review` diffs the branch against the remote primary branch (base
  resolution `origin/HEAD` → `origin/main` → `origin/master` → `main` →
  `master`; `--base` overrides). `--working` adds uncommitted edits;
  `--uncommitted` reviews only working-tree edits (exclusive with `--working`,
  no stated intent). Output `--format human` (default) / `json` / `agent`.
- `lgtmaybe config init|show|get|set|path` (`config/store.py`,
  `~/.config/lgtmaybe/config.yml`) persists non-secret defaults only. API keys
  are never persisted.
- `lgtmaybe diagram` prints the overview body using its text renderings.

## Distribution and Release

Maintainer setup: `docs/how-to/releasing.md`.

- **Release automation:** `release-please.yml` reads **conventional commits**
  on `main` and keeps a Release PR. Merging it tags, releases and publishes in
  the same run. `commitlint.yml` gates PR titles and commits so it can version.
- **PyPI** (`pip install lgtmaybe`): trusted publishing (OIDC, env `pypi`) from
  an *inline* top-level job in `release-please.yml`, so the OIDC publisher
  matches.
- **GitHub Action:** composite `action.yml` does the OIDC/WIF exchange, then
  `docker run`s the GHCR image via the `action` entrypoint (routes on
  `GITHUB_EVENT_NAME`, reads `INPUT_*`). `release.yml` pushes the image
  (`{version}`, `v{major}`, `latest`) and moves `v1`. The `Dockerfile` uses
  `uv sync --no-dev --frozen` and no uv at run time.
- **Homebrew** (`MattJColes/tap`, repo `MattJColes/homebrew-tap`, deliberately
  not `homebrew-lgtmaybe`; users must `brew tap` and `brew trust` it):
  `scripts/update-homebrew-formula.sh` writes a venv formula installing **PyPI
  wheels**, not `resource` stanzas, because litellm's Rust sdists cannot build
  in Homebrew's sandbox. It must declare **`preserve_rpath`** or install fails
  on the wheels' `@rpath` dylibs. `homebrew.yml` is **called** by release-please
  (`workflow_call`: a `GITHUB_TOKEN` release sends no `release: published`),
  also fires on `release: published` for hand-cut releases, and `brew trust`s
  and `brew install`s the formula as a gate before committing, so a broken
  formula is never published. Never bypass trust with
  `HOMEBREW_NO_REQUIRE_TAP_TRUST`: upstream is removing it, and the gate must
  cover the trust step users run. Never hand-edit the formula; it is regenerated
  each release (push via `HOMEBREW_TAP_TOKEN`; a daily schedule and `force`
  dispatch are the safety nets).
- **Windows** (`winget install MattJColes.lgtmaybe`): `windows-exe.yml` builds
  and smoke-tests a one-file Python 3.13 executable (bundles ast-grep, not the
  cloud-auth SDKs, so keyless Bedrock, Vertex and Azure need the pip install);
  `winget.yml` submits it.
- **`examples/workflows/`:** one per posting provider; cloud ones need
  `id-token: write`. ollama is local-only. Keep model IDs in docs current, in
  litellm-native form.

## Conventions

- **Docs** are **Diátaxis** (tutorial / how-to / reference / explanation),
  published via mkdocs (`docs.yml`). Human-only setup lives in `docs/how-to/`
  next to the feature it serves. mkdocs validates anchors, so `--strict` fails
  on a link to a renamed heading.
- **Generated docs: regenerate, never hand-edit.** The config reference
  (`docs/reference/config.md`) comes from `docs/generate_reference.py`
  (freshness: `tests/docs/test_reference_fresh.py`); regenerate it when you
  touch `ReviewConfig`. `docs/llms.txt` and `docs/llms-full.txt` come from `uv
  run python docs/generate_llms_txt.py` (`tests/docs/test_llms_txt_fresh.py`);
  regenerate after any docs edit.
- **Docs prose** follows `docs/how-to/write-docs.md`: written for an engineer
  new to the project, short sentences, project terms linked to
  `docs/reference/glossary.md` (acronym tooltips in
  `includes/abbreviations.md`), Australian spelling, no em dashes.
  `tests/docs/test_readability.py` gates it via `scripts/doc_readability.py`
  (its constants hold the limits the style guide quotes). Pages that do not pass
  yet sit in `NOT_YET_PASSING`, which can only shrink.
- **Contributor guides:** `DEVELOPMENT.md` (running the CLI locally, including
  an unpushed branch via `--base`) and `CONTRIBUTING.md` (tests and CI gate).

## Living Specs (OpenSpec + ast-grep Anchors)

`openspec/specs/<capability>/spec.md` are the **living domain specs**. Change
proposals are disposable; durable knowledge folds back into these. Each
requirement section carries an anchor id (`<!-- anchor: engine.snap -->` on its
own line after the opening paragraph), bound to code by ast-grep rules in the
capability's `anchors.yml`. Rules match code **shape** (node kind, name regex, a
`files:` glob). **Never use `pattern:`**: it silently misses async defs. Rules
survive moves and break on renames by design, because a rename usually changes
what the spec describes.

- **Before changing code**, resolve the anchors for the files you will touch
  (grep the `anchors.yml` sidecars, or run `uv run ast-grep scan --inline-rules
  … --json src/` from the repo root, where the `files:` globs resolve) and read
  only the matching spec sections.
- **At the end of a task**, run `uv run pytest tests/specs -q` and update the
  sections your change made stale, as targeted edits only, never a free rewrite.
  Most tasks need no spec change; that is the correct outcome.
- **Anchor hygiene:** each rule resolves to exactly one place. 0 matches is
  dangling, more than 1 is too loose (tighten with `files:` or `inside:`). Both
  fail `tests/specs/test_anchors.py`, which also checks the bijection with the
  spec's anchor ids, OpenSpec shape and a 40-line section cap (split a section
  rather than let it grow into a changelog).
- **Drift gate** (`scripts/check_spec_drift.py`, `spec-drift.yml`) is
  non-blocking by design. It warns when anchored code changed but its section
  did not, and when a rule that matched at the merge-base now matches nothing.
  Fix the rule *and* re-read the section; do not just patch the regex.
- `openspec validate --specs` must stay green (`npx -y
  @fission-ai/openspec@latest validate --specs`). Its parser reads only the
  first line of a requirement for the SHALL/MUST check, so lead with it.

## Code Quality and Dependency Hygiene

Where a check lives depends on whether it can be deterministic.

- **Deterministic: per-PR gate.** Deprecated-API use is a hard error
  (`filterwarnings = error::DeprecationWarning` in `pyproject.toml`;
  `tests/test_code_quality.py` imports every module under it and asserts the
  gate stays wired). **Do not weaken the gate** to silence third-party noise;
  add a narrow per-library `ignore`. `uv lock --check` catches lockfile drift;
  ruff `UP` rules catch outdated syntax.
- **Not deterministic: scheduled.** Newer versions and known CVEs depend on
  upstream at check time, so they are never a per-PR gate (an upstream CVE must
  not break an unrelated build): `dependabot.yml` (weekly) and `audit.yml`
  (`pip-audit`, weekly and on dependency-touching changes).
- **Model quality: on-demand eval harness.** It needs a live model, so it stays
  out of pytest (hosted providers and live ollama alike). `python -m evals.run
  --provider … --model …` reviews each fixture in `evals/fixtures/` and reports
  parse rate, recall, clean status and an `anchored` rate. `_gate` has **three
  bars**: parse, pooled recall (`--min-recall`) and clean (a fixture's
  `forbidden` findings must not appear). Plumbing is unit-tested in
  `tests/evals/`; per-lens coverage in `tests/evals/test_fixtures.py`.
  Lens-specific fixtures: `counter-advice` (ponytail), `architecture`,
  `spec-delivery`, `cross-file-fp` and `unshown-code-fp` (codebase humility),
  `cross-file-recall` (`--mid-review-retrieval`). A/B sweeps: `python -m
  evals.ab --sweep key=a,b`. Real-spend hosted e2e is label-gated in
  `action-e2e.yml`.

[litellm]: https://github.com/BerriAI/litellm
