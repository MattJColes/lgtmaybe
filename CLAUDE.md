# CLAUDE.md

Guidance for agents working in **lgtmaybe** — a provider-agnostic PR reviewer.
Read this before writing code. It encodes decisions that are **made, not options**.

## What this is

A PR reviewer that posts inline review comments + a summary. The user picks the
LLM backend with a `--provider` flag, drops a key into GitHub secrets (or wires
OIDC/WIF for cloud providers), and gets a review. One core, four distribution
variants:

- **PyPI CLI** — `pip install lgtmaybe`
- **Homebrew CLI** — `brew install MattJColes/tap/lgtmaybe` (after
  `brew tap MattJColes/tap` + `brew trust MattJColes/tap` — current
  Homebrew requires trusting third-party taps), from the
  `MattJColes/homebrew-tap` repo (Homebrew strips `homebrew-`, so the tap is
  `MattJColes/tap`; the repo is deliberately *not* named `homebrew-lgtmaybe`,
  which would make the formula `MattJColes/lgtmaybe/lgtmaybe`).
  The formula (`scripts/update-homebrew-formula.sh`)
  creates a venv and `pip install`s lgtmaybe + deps from **PyPI wheels** — *not*
  per-dependency source `resource` stanzas: litellm's tree includes Rust sdists
  (tokenizers, hf-xet) that can't build in Homebrew's sandbox, and the wheel path
  sidesteps that (and the 24h `brew update-python-resources` cooldown) entirely.
  The one wrinkle: the wheels ship prebuilt extension dylibs with `@rpath` ids
  that Homebrew can't rewrite, so the formula declares **`preserve_rpath`** to
  keep them (without it `brew install` errors "Failed to fix install linkage" and
  exits non-zero). It's a plain source formula — no bottle, works on any
  arch/macOS. `.github/workflows/homebrew.yml` regenerates the formula on each
  release (release-please **calls** it via `workflow_call`, since a `release:
  published` event isn't delivered for a `GITHUB_TOKEN` release), **actually
  `brew trust`s the tap and `brew install`s it as a gate** (so a broken formula is
  never published — and the gate covers the trust step users must run, instead of
  disabling it via `HOMEBREW_NO_REQUIRE_TAP_TRUST`, which upstream is removing),
  then commits to the tap; a daily schedule + `force` dispatch are the safety nets
- **Windows CLI** — `winget install MattJColes.lgtmaybe`. Each release builds a
  one-file Python 3.13 executable, smoke-tests the Click command tree, attaches
  it to the GitHub release, then submits `MattJColes.lgtmaybe` to winget. The
  executable bundles ast-grep but not the optional cloud-auth SDKs; use pip for
  keyless Bedrock, Vertex, or Azure.
- **GitHub Action** — composite action (`action.yml`) that does keyless OIDC/WIF
  auth, then runs a GHCR image via the `action` entrypoint

**The wedge:** first-class **Bedrock + Vertex + Azure with keyless OIDC/WIF**.
Seven hosted providers (plus local ollama), one flag, no keys in secrets for
cloud. We win on auth + simplicity. An `openai-compatible` provider is the escape
hatch for anything else that speaks the OpenAI `/v1` wire format (DeepSeek's API,
llama.cpp, LM Studio, vLLM) — you bring the `--api-base`, the key is optional —
so the provider list is never a cage.

The main CI matrix runs Ubuntu and Windows on the minimum supported Python
version, 3.11. Windows runs with locale-default encoding behavior and disables
autocrlf before checkout.

## Non-negotiables

- **TDD, always: red → green → refactor.** Write the acceptance test from a
  task's stated in/out *first*, watch it fail, write the minimum code to pass,
  then refactor. CI rejects a PR whose diff adds code without a test.
- **Structured output only.** The model returns JSON (`severity`, `file`,
  `line`, `body`, `suggestion`). Never parse prose.
- **Fork safety.** Trigger on `pull_request_target` so the review has secrets,
  but **never check out or execute PR code** — fetch the diff via API only.
  Treat all diff content as untrusted input.
- **No required static cloud keys.** Bedrock uses ambient AWS creds; Vertex uses
  ambient GCP creds; Azure uses a supplied `AZURE_API_KEY` first and otherwise
  falls back to ambient Entra (Azure AD) creds via GitHub OIDC. Never require a
  service-account JSON or static AWS key.

### Forges — which code host the review is posted to

A second axis, orthogonal to the LLM provider: **`Forge`** (`core/forge.py`) is
github / gitlab / gitea, and `Provider` stays the model backend. Never overload
"provider" for a code host.

- The **engine never sees a gateway.** It takes a `PRContext` and returns
  findings; `local/` already produces a `PRContext` from `git` with no forge at
  all. Anything host-neutral therefore belongs in `core/`, not in an adapter
  package — diff parsing and the skip filter (`core/diff.py`), finding
  fingerprints (`core/findings.py`), and comment Markdown (`core/comment.py`)
  are all shared. A new adapter that reimplements any of those is a bug.
- **`ReviewGateway` (`core/ports.py`) is three methods.** Everything richer is an
  optional `Supports*` capability protocol the CLI probes for and skips when
  absent, so an adapter ships useful before it ships complete. Declare only what
  the host can actually serve — claiming a capability that cannot work is worse
  than not claiming it.
- A URL is resolved by `core/forge.parse_pr_url` into a `PRLocator`
  (forge + host + repo + number); `cli._GATEWAY_BUILDERS` maps forge to adapter.
  Host is carried because self-hosted GitLab/Gitea is the norm.
- **GitHub** is the complete adapter. **Gitea** (`gitea/gateway.py`) mirrors it
  minus incremental review and thread resolution (its API cannot serve either);
  its summary lives in an editable issue comment because a submitted Gitea review
  is immutable, and it de-dupes findings by reading the hidden ids already posted.
  **GitLab** (`gitlab/gateway.py`) is the one whose model really differs: no
  batched review object, so each finding is its own *discussion* positioned by
  old/new path + line and the MR's three diff refs; the summary is an upserted
  note; thread resolution works over plain REST (no GraphQL), gated on the
  caller's validated allowlist because GitLab exposes no "lines moved" signal.
  Incremental is unbuilt there — a "not yet", not a "cannot".
- **Entrypoints:** GitHub and Gitea Actions share `action` (Gitea reimplements
  the same env contract; only `GITHUB_SERVER_URL` differs, and
  `cli._change_url` reads it). GitLab CI has neither an event payload nor
  `INPUT_*`, so it gets its own `gitlab-ci` command reading `CI_*`.

## Key decisions (do not relitigate)

- **Language:** Python.
- **Provider spine:** [litellm] — normalises openai, openrouter, anthropic,
  bedrock, vertex, azure, ollama to one `completion()` call. A thin wrapper on
  top adds retries / fallback.
- **License:** MIT (already in `LICENSE`).
- **Posting:** REST review API — batched inline comments + one summary. Every
  posted finding's title line carries its provenance inside the severity
  brackets — `**[HIGH · security · 80%] Title**`, the originating lens and the
  reflection auditor's confidence as a percentage of its 0-10 score
  (`rest_gateway._finding_badge`, each half
  omitted when absent; inline, demoted, and broad render it identically). It is
  visible prose only — never part of the hidden ids below.
  Idempotent updates via a hidden marker comment (which also carries the
  last-reviewed-SHA watermark driving incremental review). Each inline comment also carries
  **two** hidden per-finding ids: `finding_fingerprint(path, title)` — which keys
  the user-facing channels (`ignore_fingerprints`, 👎 feedback) — and
  `finding_identity(path, category, anchor)`, which carries **no model prose**.
  Both re-run dedupe and resolve-on-fix match on **either**: the fingerprint
  hashes the title, so it changes whenever the model rewords the same finding,
  and keyed on it alone a re-run re-posts findings it already made. On a
  re-run, conversations whose finding is gone **and** whose thread GitHub marks
  outdated are replied to and resolved (`ReviewConfig.resolve_fixed`, default on).
  Resolving a thread is the one op the REST review API can't do, so it uses the
  GraphQL API (`resolveReviewThread` / `addPullRequestReviewThreadReply`) —
  best-effort, never fails the review.

### Auth model — resolved by provider (chain of responsibility)

| Provider               | Auth                                                              |
|------------------------|------------------------------------------------------------------|
| openai / openrouter / anthropic | API key from `secrets.*` / env / `--api-key`            |
| zai (GLM / Zhipu AI)   | API key (`ZAI_API_KEY` / `--api-key`); litellm-native `zai/` route. Optional `--api-base` / `ZAI_API_BASE` override for the China / coding-plan endpoint |
| bedrock                | ambient AWS creds (GitHub OIDC role, or local `~/.aws`); IAM `bedrock:InvokeModel*` only |
| vertex                 | ambient GCP creds (WIF, or local ADC)                            |
| azure                  | needs the resource endpoint (`--api-base` / `AZURE_API_BASE`); `AZURE_API_KEY` / `--api-key` when supplied → else ambient Entra creds (GitHub OIDC federation via `azure/login`, or local `az login` / managed identity) |
| ollama                 | none — just an `api_base` (localhost, host.docker.internal, tailscale host); fully local, zero cost |
| openai-compatible      | requires the endpoint (`--api-base` / `OPENAI_COMPATIBLE_API_BASE`); key **optional** — `--api-key` / `OPENAI_COMPATIBLE_API_KEY`, else a placeholder for keyless local servers (llama.cpp / LM Studio / vLLM). litellm `openai/` route to a custom base |

Resolver order: chosen provider → use its API key when supported and supplied →
else try ambient cloud creds if that's its native mode → ollama needs neither →
openai-compatible needs an `api_base` (key optional, placeholder when absent) →
else **fail with a clear
"how to auth this provider" message**.

## Architecture — ports & adapters (hexagonal)

This is what lets tracks build in parallel against frozen contracts.

- `core/ports.py` — the ports (interfaces). **Frozen in the foundation step.**
- litellm / github classes — the adapters.
- **Engine is a pipeline:** `fetch → compress → prompt → parse → re-anchor →
  merge/dedupe → reflect → filter → post`, as composable stages. The prompt/parse
  stage **fans out per `ReviewCategory`** — one concurrent model call per lens —
  then merges + de-dupes the findings, and a **self-reflection pass**
  (`engine/reflect.py`) drops the model's own low-confidence findings before posting.
- **Line anchoring (don't trust model arithmetic):** LLMs miscount diff line
  numbers, so every finding carries a verbatim `anchor` (the flagged line, no
  +/- marker). After parse, `engine._snap_findings` re-anchors `line` to the real
  changed line whose content matches the anchor (`core/diffparse.changed_line_index`;
  exact → whitespace-normalised → unique-substring match, nearest-to-model-line
  tiebreak). When an anchor matches **nothing**, the line is a guess: the finding
  is marked `anchored=False` and the GitHub adapter **demotes it to the review body**
  (`rest_gateway._render_demoted`) rather than post an inline comment on a wrong
  line — a wrong-line comment breaks trust faster than a finding without a precise
  line. No anchor → trust the model's line (back-compat). The on-demand eval
  harness reports an `anchored` rate per fixture so the match rate is measurable.
- **Provider choice:** strategy + factory. The `--provider` flag selects a
  strategy; a small factory builds the `ProviderClient` (litellm keeps it tiny).
- **Credential resolution:** chain of responsibility (see auth table).
- **Dependency injection:** inject ports into the engine — this is what makes
  fakes + dry-run drop in.

**Deliberately skipped** (don't add without a written reason): repository
pattern, event bus, plugin framework.

## Parallel build structure

1. **Foundation (sequential, first):** freeze the contracts in `core/ports.py`,
   plus structured logging for CI debugging. Everything downstream codes against
   these frozen ports.
2. **Parallel tracks**, each against frozen contracts:
   - **Track A** — provider/litellm wrapper: retries, fallback.
   - **Track B** — github adapter + diff handling; **skip generated/binary files**
     (lockfiles, minified, vendored).
   - **Track C** — hardening: **prompt-injection defense** (PR text trying to
     steer the reviewer — `engine/injection.py` wraps the diff as untrusted data
     **and neutralises forged `DIFF_START`/`DIFF_END` delimiters** so an attacker
     diff can't break out of the data block; the stated-intent block gets the
     same treatment via `wrap_intent`, with both marker families neutralised in
     both blocks), **secret redaction in diffs before
     they leave for the LLM** (`engine/redact.py` covers AWS/OpenAI/GitHub
     (classic + fine-grained)/Slack/Google/Stripe keys, PEM private-key blocks,
     and quoted password / `Authorization` / connection-string credentials),
     fork-PR exposure (already handled by `pull_request_target` + no checkout).
   - **CLI track** — PyPI packaging; a local `lgtmaybe review` of your `git` diff
     (prints findings, no GitHub) for local dev. Diffs the branch against the
     remote primary branch — base resolution `origin/HEAD` → `origin/main` →
     `origin/master` → `main` → `master` (`--base` overrides); `--working`
     reviews the whole worktree (branch commits + uncommitted edits) against the
     merge-base with that same base; `--uncommitted` reviews only the
     working-tree edits vs HEAD (mutually exclusive with `--working`, no stated
     intent); commit subjects vs the base feed the intent lens in branch and
     working mode. Output `--format human` (default) / `json` (`--json`) / `agent`
     (correction instructions an AI coding agent can read and apply). Non-secret
     defaults (provider, model, severity floor, caps) persist in a user-level
     config — `lgtmaybe config init|show|get|set|path` (`config/store.py`,
     `~/.config/lgtmaybe/config.yml`); **API keys are never persisted** — they
     stay in the environment.
3. **Integration (sequential, last) — DONE:** the tracks are wired together.
   `cli.build_review_context` swaps the fakes for the real `LiteLLMProvider` +
   `RestGitHubGateway`; `python -m lgtmaybe` (the Docker ENTRYPOINT) is the live
   Click CLI. Delivered in this step:
   - **`review` command** — full PR review, posts inline comments + summary.
   - **`comment` command** — handles the `issue_comment` event and routes slash
     commands to the same engine/provider: `/review` + `/improve` post a review,
     `/ask <q>` replies in-thread (`post_issue_comment`, an adapter-only method
     beyond the frozen port), and `/describe` posts a **standalone structured
     description** (`engine/describe.py`: title, change type, summary, per-file
     walkthrough table, intent check when the PR states one; structured output
     with a raw-text fallback) via `post_describe_comment` — an idempotent upsert
     that edits our previous description in place.
   - **The change overview (`/diagram`, `auto_diagram`)** — ONE comment via
     `post_diagram_comment` (its own idempotent upsert + disjoint marker family),
     composed by `engine/overview.py` from **three concurrent structured calls**
     on one `ThreadPoolExecutor`, in reading order:
     1. the **description** (the same `engine/describe.py` call, `describe_result`
        for the typed object; `ReviewConfig.auto_describe`, default **on** — it
        rides this comment, so it refreshes on every push);
     2. **High Impact Areas** (`engine/high_impact.py`, `ReviewConfig.high_impact`,
        default **on**) — a bold `### **High Impact Areas**` section answering
        "what could this break beyond itself" over ten areas (infrastructure,
        security, availability/outage, data_migration, backup_and_recovery,
        compatibility, observability, dependencies, cost, compliance). Two
        sources: the model, plus **deterministic path signals** (`path_signals`,
        per-area regexes) that ground the call as untrusted hints
        (`injection.wrap_path_signals`, its own neutralised `SIGNALS` family —
        filenames are attacker-chosen on a fork PR) **and floor the output**: an
        area with signals the model ignored is still listed "(not assessed by the
        model)", and a failed/unparseable call renders the floor alone. An empty
        result names what was checked rather than vanishing. Model-chosen paths
        not in `changed_files` are dropped;
     3. the **diagrams** (`engine/diagram.py`: from one structured call returning
        typed nodes/edges/steps, a Mermaid **flowchart** of what the PR touches
        *and* a Mermaid **sequence diagram** of the run-time flow it alters —
        structure answers "what does this touch", sequence "what happens, in what
        order"; the sequence view is omitted when the model returns no steps.
        Both render natively on GitHub, each with a text rendering that is the
        terminal view and the fallback; model-authored Mermaid never reaches a
        fence, and sequence labels are escaped with Mermaid entity codes).
     The description heads the comment (the diagram's own title is suppressed);
     with `auto_describe` off the diagram header returns. Sections 1 and 2 are
     **best-effort** — a failure renders a visible "unavailable" line, never a
     silent gap — while the diagram call keeps propagating, because the automatic
     overview is a **required completion step** (a failure must not stamp the head
     complete). With both sections off, the body is byte-identical to
     `build_diagram` and costs one call. `auto_diagram` (Action input, default
     **on**) is the one switch for the whole comment; `should_auto_diagram` gates
     it to opened/reopened/synchronize. The local `lgtmaybe diagram` command prints
     the same body (no GitHub) — a terminal can't render Mermaid, which is what the
     text rendering is for. **No D2:** GitHub doesn't render it in Markdown.
     The shared one-call scaffold is `describe.structured_call` (typed result) /
     `structured_comment` (rendered Markdown); `describe.markdown_text` escapes
     every section's model prose.
   - **Guards (in the engine):** generated/binary files skipped via
     `is_reviewable`; the user's `include_paths` allowlist / `exclude_paths`
     denylist globs applied right after it (`engine.passes_path_filters`;
     exclude wins, `**/`-prefixed patterns also match at the repo root);
     **file cap** reviews the top-N and posts a "reviewed top N of M" notice.
   - **Context expansion:** `get_pr_context` also fetches the head text of
     reviewable files via the API (read-only, never a checkout) into
     `PRContext.file_contents`; the engine (`compress.expand_hunks`) pads each
     hunk with budget-scaled surrounding lines, capped by
     `ReviewConfig.context_lines` (default 20, `0` disables), redacted like the
     diff. The pad is **asymmetric** (`compress.trailing_context_lines`): the
     full budget goes before each hunk, a quarter of it (floored at one line)
     after — the enclosing signature/setup explains a change better than what
     follows. Inline positions stay bound to the **real** diff, so a finding on a
     context-only line maps to nothing and is dropped — never mis-posted.
   - **Recursive walk (RLM):** when a single file's diff exceeds
     `max_input_tokens`, the engine **walks it hunk-by-hunk** instead of sending it
     whole (where the model's context drops the tail) — `compress.split_patch_into_hunks`
     decomposes the over-budget file into per-hunk mini-diffs (each carrying its
     file header, so finding line/side still bind to the real diff) that
     `batch_files(recursive=True)` then batches normally. Nothing is dropped and
     each call's context stays small — better recall on big files, especially for
     smaller models. Files within budget are reviewed whole (context preserved).
     `ReviewConfig.recursive` (default **on**; CLI `--recursive/--no-recursive`,
     Action input `recursive`); the on-demand A/B benchmark `python -m evals.ab
     --sweep recursive=false,true`
     measures recall + token cost of the walk vs sending whole against a live model.
   - **Incremental review (commit-scoped):** on a re-run, `run_review` reads a
     hidden watermark (`<!-- lgtmaybe-reviewed:<head_sha> -->`, stamped into the
     summary comment by `mark_reviewed` on the success path only — a failure
     never moves it) and reviews only the compare-API diff of the commits since
     (`rest_gateway.compare_diff`, read-only). Falls back to a full review on
     no-watermark / force-push (compare not "ahead") / same head / API failure.
     New findings post as individual review comments (fingerprint-deduped —
     the review-update endpoint can't add inline comments); resolve-on-fix is
     scoped to the increment's files (`set_incremental_scope`) so an
     un-re-reviewed finding is never spuriously resolved; LEFT-side findings
     are dropped (their line numbers are relative to the last-reviewed head,
     not the PR base). `ReviewConfig.incremental` (default None = auto: on for
     the Action's `synchronize` event, full elsewhere; Action input
     `incremental`); `/review full` forces a full re-review.
   - **Static-analysis fusion (default off):** with `static_analysis.enabled`,
     installed deterministic tools (the eight in `StaticAnalysisTool` — ruff,
     bandit, mypy, semgrep-with-local-rules, gitleaks, zizmor, ast-grep,
     osv-scanner — `pip install lgtmaybe[static-analysis]`) run over the already-fetched
     changed-file texts in a temp dir (`engine/static_analysis.py`; sandboxed
     subprocess: scrubbed env, no network — semgrep only ever runs with local
     `semgrep_rules`, never `--config auto` — hard timeout, never a checkout).
     Findings are mapped onto the shared Severity scale, floored by
     `static_analysis.min_severity`, redacted, and wrapped as an untrusted
     HINTS block (`injection.wrap_hints`, its own neutralised marker family)
     prepended to each batch's lens calls — "confirm, contextualise, or
     discard"; raw tool findings are never posted. A missing tool is skipped
     silently. CLI `--static-analysis/--no-static-analysis`, Action input
     `static_analysis`.
   - **Two-stage triage (default off):** with `triage_model` set, that cheap
     model runs first (`engine/triage.py`) to skip plainly-non-substantive
     files and rank the rest by risk; the strong `model` reviews only the
     survivors, riskiest first. A deterministic security floor
     (`triage.always_escalate`: security paths/tokens, static-analysis hits,
     large hunks) always escalates past triage, any triage failure reviews
     everything, skips are named in the summary, and `/review full` bypasses
     both triage and incremental scoping. All model slots (`triage_model`,
     `model`, `reflect_model`) share one provider/credentials. CLI
     `--triage-model`, Action input `triage_model`.
   - **Truncation ladder (escalation is LAST):** a truncated lens is first given
     the remedy its token counts named, on the model the user chose —
     `_review_split` (smaller pieces) when the answer outgrew the ceiling,
     `_retry_lower_effort` when the *thinking* did. Only if that fails does
     `engine._escalate_model` re-run the lens once on `fallback_model`. Switching
     model says nothing about the failure — it re-sends the same request at the
     same ceiling — so it is the last rung, spent by the whole batch and never by
     each split piece. Lens calls carry `defer_truncation` so the adapter hands a
     truncation back instead of falling back beneath the engine (every other
     failure it still rescues itself, unchanged). `fallback_model` is now a
     `ReviewConfig` field too — `--fallback-model` / the Action input still win.
     Which model answered rides home on `ProviderResult.model`, which is what
     makes both paths disclosable: `engine._note_answering_model` compares it
     against the provider's own model and the summary names the lens and the
     rescuer, and `--profile` prints a `models:` line whenever a run used more
     than one. **Do not route on diff size instead**: truncation does not track
     size (measured — a fifteen-line diff truncates at the same ceiling as a
     large multi-file one, because the ceiling goes on reasoning), so a size
     threshold spends the strong model on the diffs that were already fine.
   - **Error surfacing:** any failure posts a short "review failed" comment and
     the CLI exits non-zero (`ClickException`) — never fails silently.
   - **Per-lens fan-out (preset-shaped):** the prompt is composed per lens
     (`engine/prompt.py`) — each lens gets a **worked example** (with a real
     hunk header, teaching the line-number arithmetic). `ReviewConfig.preset`
     picks the lens set: **`fast` (default)** covers all nine categories in
     **four calls, one per concern** — dedicated security, dedicated
     correctness (stated intent folds in, with per-finding `category`
     attribution), merged code-health, and merged artefacts (tests +
     documentation); the two merged lenses are `prompt.FAST_GROUPS`. The same
     four run on **every** provider: worker count decides only whether they
     overlap, never how many there are. **`full`** runs one call per category.
     An explicit `categories` list overrides the grouping. Every (batch, lens) call runs through **one global
     `ThreadPoolExecutor`** sized by `ReviewConfig.max_concurrency` (auto: 6
     for every provider), then the findings are **merged and
     de-duped** (`engine._dedupe`, keyed on path/line/side) before reflection.
     A soft whole-review deadline (`max_review_seconds`, default 3600s, 0 = off)
     skips still-queued calls once passed — partial results with a notice,
     never a silent LGTM. A **termination signal** (SIGINT/SIGTERM from a
     cancelled or timed-out CI job) sets that same state via
     `engine.request_interrupt`, so an interrupted run posts what it has
     instead of nothing; the handler is installed by the CLI entrypoint only
     (`cli.graceful_interrupt` — never at library import), restores the
     previous handler as it fires (a second signal still kills), and is a
     no-op off the main thread. Every stage and call is timed
     (`engine/profiling.py`); `--profile` / Action input `profile` prints the
     breakdown.
   - **Custom lenses (BYO):** beyond the built-in `ReviewCategory` set, users add
     their own lenses via `ReviewConfig.extra_lenses` (a `CustomLens`: `id` +
     `instructions`, optional `title` and a worked `example_diff`/`example_finding`)
     — defined inline in `.lgtmaybe.yml` or in skill files loaded by the config
     loader's `lens_paths` directive. The engine builds a uniform `_Lens` per
     built-in category **and** per custom lens (`engine._build_lenses`,
     `prompt.build_custom_lens_block`) and fans them all out identically through the same
     merge/dedupe/reflect pipeline. Lens text enters the system prompt, so it is
     **trusted config only** — never sourced from PR-author content (on
     `pull_request_target` config comes from the base, not the PR head). Covered by
     `tests/engine/test_prompt.py`, `tests/engine/test_engine.py`,
     `tests/config/test_loader.py`, and `tests/test_models.py`.
   - **Directory-scoped instructions + context (BYO, per path):**
     `ReviewConfig.directory_rules` (a `DirectoryRule`: `paths` globs — empty =
     everywhere — plus `instructions` and `context_files`) lets a monorepo say
     "`payments/**` is strict, `tests/**` is lenient, read `ARCHITECTURE.md`
     before `src/**`". `engine/directory.py` reuses `passes_path_filters` to
     match a batch, `retrieve.resolve_needs` with a **local-filesystem** fetcher
     to read the context files (inheriting redaction, the `max_input_tokens // 8`
     budget, and `MAX_FETCH_FILES`), and renders one block. The block joins the
     **same per-batch prefix string** as the hints + wrapped diff — not a fourth
     message — so `build_shared_preamble` stays byte-identical, the existing
     primer warms it once, and the adapter needs no change. Context comes from
     the checked-out workspace (`Path.cwd()`), i.e. the **base** branch on
     `pull_request_target` — never `github.get_file_contents`, which resolves at
     the PR head. Instructions ride verbatim under a trusted-config lead-in;
     context text is `injection.neutralise`d. YAML-only (list-of-objects, like
     `finding_rules`): no CLI flag, no Action input. **No `BUGBOT.md` walk-up** —
     it stays available later as a pure loader desugar needing no engine change.
   - **Intent lens:** "does the PR do what it says?" — `PRContext` carries the
     stated intent (`title`, `description`, `commit_messages`): PR title/body +
     commit names via the REST gateway, or `git log` commit names from the local
     CLI (`local/_commit_subjects`), so it works without GitHub. The engine
     redacts the intent text, wraps it via `injection.wrap_intent` (its own
     neutralised `INTENT_START`/`INTENT_END` block), and sends it **only on the
     intent call**; with no stated intent the lens is skipped (logged, no notice).
   - **Not-shown manifest (every lens):** `engine.files_not_visible` derives — by
     subtraction, so one call covers all seven ways a file goes missing — the PR's
     changed files this batch's diff does not show. It rides the intent and spec
     blocks with their own lens-specific lead-ins, **and** `injection.wrap_not_shown`
     puts it on the **per-batch cacheable prefix that every lens reads** (its own
     neutralised `HIDDEN_START`/`HIDDEN_END` family — paths are attacker-chosen on a
     fork PR). Every other lens used to *infer* absence from the shared humility
     rule's "code you rely on may live in files you CANNOT see", which asks the
     model to reason about what it cannot observe; naming the files makes it a
     stated fact instead. Nothing hidden ⇒ `None`, so the common case adds zero
     prompt bytes and leaves the cached prefix byte-identical. The naming cap
     (`_MAX_LISTED_NOT_VISIBLE`) is applied in **one** place for all three blocks.
     File granularity only — it cannot say "this file is shown but that hunk is
     not", which is the same-file shape the prompt's value rule covers instead.
   - **Spec lens (default on, detection-gated):** "does the PR deliver the
     specification it commits to?" — `engine/specs.py` detects a committed spec
     (**OpenSpec** `openspec/changes|specs/*`, archives excluded; **Spec Kit**
     `.specify/` or `specs/*/` with spec.md **and** plan.md; **Kiro**
     `.kiro/specs/*`; plus `ReviewConfig.spec_paths` globs) with a pure
     filesystem probe, then **ranks** the candidates against the PR (it edits the
     spec / the branch names it / the stated intent names it; a lone spec dir
     wins by default) and sends at most `MAX_SELECTED` = 2. Reports both
     directions: the diff falling short of the spec, **and** behaviour the spec
     never covers (the "what did the spec miss" half). `specs.ticked_tasks`
     mines the diff for `- [ ]` → `- [x]` flips in a `tasks.md` — the author's
     own delivery claims, extracted with no model call — and renders them as
     claims to verify. Files the PR changes are read from `PRContext.file_contents`
     (head text: a spec is usually committed in the PR that implements it), the
     rest from the workspace (`LLMReviewEngine(workspace_root=...)`, default
     `Path.cwd()` = the trusted base branch on `pull_request_target`), through
     `retrieve.resolve_needs` for redaction + a `max_input_tokens // 8` budget.
     Redacted once, wrapped per batch via `injection.wrap_spec` (its own
     neutralised `SPEC_START`/`SPEC_END` family) carrying the batch's
     `files_not_visible` list — a requirement delivered elsewhere is *not shown,
     not undelivered*. Sent **only on the spec call**, which is its own lens in
     both presets (a **fifth** `fast` call, paid only when a spec matched);
     nothing detected or nothing matched skips it entirely (logged, zero prompt
     bytes). `reflect.py`'s gap-finding carve-out names spec mismatches, or the
     auditor prunes them as cross-file absence claims. CLI `--spec/--no-spec`,
     Action input `spec_review`; eval fixture `spec-delivery`.
   - **Ponytail lens:** the "lazy senior dev" lens (`ReviewCategory.ponytail`),
     inspired by the Ponytail skill — *the best code is the code you never wrote*.
     Flags code that needn't exist at all (YAGNI / speculative generality,
     reinventing the stdlib, code that could be far shorter, premature
     configurability), restrained at `info`/`medium`. Distinct from `complexity`
     ("is this hard to follow?"): ponytail asks "should this exist at all?".
     Default-on like the other built-ins; asserted by
     `test_prompt.py::test_prompt_asks_for_ponytail_review`.
   - **Mid-review retrieval (lens deferral, default off):** with
     `ReviewConfig.mid_review_retrieval`, a lens may answer `needs` beside its
     `findings` — the paths/symbols it must read — instead of hedging or omitting
     a claim that hinges on unshown code (which the shared humility rule
     otherwise requires). `engine._review_with_context` fetches them through the
     SAME read-only, redacting boundary reflection's deferral uses
     (`retrieve.resolve_needs`, `MAX_FETCH_FILES`, `max_input_tokens // 4`; never
     a checkout) and re-runs **that one lens** with the text
     `injection.wrap_context`-wrapped on its own **uncached lens block** — never
     the shared prefix, which its sibling lenses read from cache. Bounded to
     **one hop** (the re-run passes `batch=None`, the same condition that bounds
     the timeout split), the ceilings are re-checked via `_skip_reason`, and both
     calls' findings merge into the existing dedupe — a deferral can only add
     findings. The prompt ask is gated by `prompt.retrieval_rules`, so off is a
     zero-byte prompt change. Off by default because the worst case is one extra
     call per (batch, lens) and the recall win is unmeasured — the
     `cross-file-recall` eval fixture and `python -m evals.run
     --mid-review-retrieval` are how that gets measured.
   - **Self-reflection:** after merge/dedupe, `engine/reflect.py` asks the
     provider to audit its own findings for false positives and drops the ones it
     marks low-confidence. The verdict is structured (`ReflectionResult` —
     `{"verdicts": [{"index", "keep", "confidence", "broad", "needs"}]}`) with a
     lenient parser and a **keep-all safe default** when it can't be parsed
     (never silently drop a real finding). Each kept verdict carries a **0–10
     confidence score** (the auditor tries to disprove the finding to reach it);
     `ReviewConfig.min_confidence` (default 0 = off, CLI `--min-confidence`)
     drops findings scored below it, an unscored kept finding survives any
     threshold, and the score lands on `ReviewFinding.confidence` in CLI/JSON
     output. Skippable via `--no-reflect` for weaker models that over-prune. The auditor
     also drops **cross-file false positives** — findings whose validity hinges on
     an assumption about code outside the diff (a guard/field/handler that may live
     in an unshown file) — while **carving out gap findings** (performance,
     complexity, intent, spec). The test/doc half of that carve-out is
     **conditional**: a "no test for this" finding keeps its protection only when
     the test or doc file is shown in the diff or in the grounded file text.
     Unconditional, it instructed the auditor *not* to prune the exact claim the
     cross-file rule forbids — a test living in an untouched file is not a missing
     test — which is the seam a real false positive landed in. This mirrors the
     shared review rule (below) that tells every lens the diff is only a **slice of
     the codebase**, so it should hedge a cross-file absence-claim and lower its
     severity rather than assert it — a rule that covers both a symbol's
     **existence** and its **value**: you may not assume what an unshown constant,
     default, config entry or collection *contains*, only that it exists (a
     constant named for a policy may hold an empty set, hundreds of lines away in
     the same file).
   - **Determinism & timeouts:** `temperature` defaults to `0.0` for reproducible
     reviews; `timeout` is `None` → a provider-aware default (ollama gets a long
     one, cloud a short one). Both are `ReviewConfig` fields and CLI/Action inputs.
   - **Prompt caching (split prefix):** every review call is shaped as a shared
     cacheable prefix — a lens-independent
     system preamble (`prompt.build_shared_preamble`), then the wrapped diff
     (+hints) as the first user block — with the lens checklist + example as
     the final uncached user block. On routes with an explicit cache breakpoint
     (anthropic `cache_control`; bedrock, where litellm emits a Converse
     `cachePoint` for Claude/Nova) the adapter merges the user blocks into
     content blocks with breakpoints on the system prompt and the last prefix
     block (cumulative 1,024-token minimum), so lenses 2..N read the whole
     preamble-plus-diff from cache; a per-batch **warm-up primer** (gated at
     ~2k diff tokens) runs one lens alone so a concurrent first wave doesn't
     pay N cache writes. Reflection splits the same way, so deferral re-judges
     read the audit's system+diff prefix. Feature-detected per model (litellm
     capability map); every other provider gets the user blocks joined back
     into one plain user message. Cache read/creation token counts land on
     `ProviderResult` and are logged. There is one prompt shape and no opt-out.
   - **Summary line:** names the **model** used (no cost — lgtmaybe does not
     compute or report cost). `ReviewConfig.summary_template` (default None)
     lets teams restyle it with `{count}`/`{provider}`/`{model}` placeholders;
     a bad template falls back to the built-in line.
   - **Finding rules (F5b):** `ReviewConfig.finding_rules` — ordered
     declarative match (path glob / lens `category` / `title_contains` /
     `min_severity`, ANDed) → action (`drop` / `set_severity`), applied in
     `engine/rules.py` just before posting. Deliberately NOT an arbitrary
     hook: no user code ever runs. Findings carry an engine-stamped
     `category` (the originating lens id) that rules and labels key on.
   - **PR labels (F4):** `ReviewConfig.pr_labels` (default off; Action input
     `pr_labels`) — `engine/labels.py` derives `review-effort/1-5`,
     `possible-security-issue` (high/critical security-lens finding), and
     `consider-splitting` (sprawling diff) from data already computed; the
     gateway reconciles only lgtmaybe's own label families, best-effort.
   - **Clean review:** zero findings on a fully-reviewed PR posts `👍 LGTM!`
     (comment only — no GitHub approval state) — still naming the model.
4. **Packaging (sequential, last) — DONE:** the four distribution variants over
   one core. Delivered in this step:
   - **`action` entrypoint** — the container command. Routes by
     `GITHUB_EVENT_NAME` (`issue_comment` → slash command, else → full review with
     the PR URL derived from the event), reads inputs from `INPUT_*`. The `review`
     / `comment` / `action` commands share `execute_review` / `execute_comment`.
     `--fallback-model` threads through to the provider.
   - **`action.yml`** — composite action; keyless cloud auth built in (pass
     `aws_role_arn` / `gcp_wif_provider` / `azure_client_id` and it runs the
     OIDC/WIF exchange), then `docker run`s the GHCR image. Inputs: provider,
     model, fallback_model, api_key, api_base, timeout, temperature,
     aws_role_arn, aws_region, gcp_wif_provider, gcp_service_account,
     azure_client_id, azure_tenant_id, config_path (+ token/image).
   - **`Dockerfile`** — lean runtime: `uv sync --no-dev --frozen`, venv on PATH,
     `python -m lgtmaybe` (no uv at run time).
   - **Release automation** — `.github/workflows/release-please.yml` reads
     **conventional commits** on `main` and maintains a Release PR that bumps the
     version + regenerates `CHANGELOG.md` (`release-please-config.json` /
     `.release-please-manifest.json`). Merging that PR cuts the tag + GitHub
     release; the same run then publishes — **PyPI trusted publishing** (OIDC, env
     `pypi`, an *inline* top-level job so the OIDC publisher matches
     `release-please.yml`) and the reusable `.github/workflows/release.yml`, which
     pushes the GHCR image (`{version}`, `v{major}`, `latest`) + moves the floating
     `v1`. `.github/workflows/commitlint.yml` (`commitlint.config.cjs`) gates PR
     titles/commits to conventional-commit format so the automation can version.
   - **Homebrew tap** — `.github/workflows/homebrew.yml` fires on the published
     release and regenerates the formula in the `MattJColes/homebrew-tap`
     tap via `scripts/update-homebrew-formula.sh` (resolves the PyPI sdist
     url/sha, then `brew update-python-resources` fills the full dependency tree;
     pushes with the `HOMEBREW_TAP_TOKEN` PAT). Regenerated wholesale each release
     so dep bumps flow through — never hand-edited. Maintainer setup (tap repo +
     PAT) is in `docs/how-to/releasing.md`.
   - **Windows executable + winget** — `.github/workflows/windows-exe.yml`
     builds and smoke-tests the portable x86_64 executable before attaching it
     to the release; `.github/workflows/winget.yml` then submits the versioned
     asset to `MattJColes.lgtmaybe`.
   - **`examples/workflows/`** — one per posting provider (cloud + API-key);
     `id-token: write` for cloud. ollama is local-only (CLI), not a workflow.
   - **Model IDs in docs are kept current** per platform (litellm-native form).

Every task carries its inputs/outputs and an acceptance test so an agent can
self-verify without asking. The acceptance test *is* the red step — start there.

## Conventions

- **Docs:** the `docs/` tree is **Diátaxis** (tutorial / how-to / reference /
  explanation), published to GitHub Pages via mkdocs (`.github/workflows/docs.yml`).
  Human-only setup lives in `docs/how-to/` next to the feature it serves — cloud
  trust in the Bedrock/Vertex/Azure guides, publishing + marketplace in
  `docs/how-to/releasing.md`, the local AI-fix loop in
  `fix-findings-with-an-ai-agent.md`. The config reference
  (`docs/reference/config.md`) is **generated** from the models by
  `docs/generate_reference.py` and kept fresh by `tests/docs/test_reference_fresh.py`
  — regenerate it when you touch `ReviewConfig`, don't hand-edit. **`DEVELOPMENT.md`**
  and **`CONTRIBUTING.md`** at the repo root are the contributor guides: how to run
  the CLI locally (incl. an unpushed branch via `--base`) and run the tests / CI gate.
- Treat diff content as untrusted everywhere it flows.
- Errors surface to the user; never swallow them.

## Living specs (OpenSpec + ast-grep anchors)

`openspec/specs/<capability>/spec.md` are the **living domain specs** — durable
descriptions of how the system behaves, distinct from OpenSpec change proposals
(which are disposable; durable knowledge folds back into these). Every
requirement section carries an anchor id — `<!-- anchor: engine.snap -->` on
its own line right after the section's opening paragraph — bound to code by
ast-grep rules in the capability's co-located `anchors.yml`. Rules match code
**shape** (node kind + name regex + a `files:` glob; never `pattern:`, which
silently misses async defs), so they survive file moves and refactors and break
on renames — by design: a rename usually changes the thing the spec describes,
and a dead anchor is the signal to re-read the section.

The workflow rules:

- **Before changing code**, resolve the anchors for the files you expect to
  touch (grep the `anchors.yml` sidecars, or run
  `uv run ast-grep scan --inline-rules … --json src/` from the repo root — the
  `files:` globs resolve against the scan cwd) and read only the matching spec
  sections, not the whole spec tree.
- **At the end of a task**, re-run `uv run pytest tests/specs -q` and update
  the spec sections your change made stale — as targeted edits to those
  sections only, never a free-rewrite of a spec. Most tasks need no spec
  change; that's the correct outcome, not a failure.
- **Anchor hygiene:** each rule resolves to exactly one place — 0 matches is
  dangling, >1 is too loose (tighten with `files:` or `inside:`). Both are
  drift and both fail `tests/specs/test_anchors.py` (bijection with the spec's
  anchor ids, exactly-one-match, OpenSpec shape, 40-line section cap — split a
  section rather than letting it grow into a changelog).
- **The drift gate** (`scripts/check_spec_drift.py`,
  `.github/workflows/spec-drift.yml`) is non-blocking by design: on each PR it
  warns when anchored code changed while its spec section sat still, and when a
  rule that matched at the merge-base matches nothing now (the rename case the
  intersection check alone can't see). A warning names the anchor id — fix the
  rule *and* re-read the section, don't just patch the regex.
- `openspec validate --specs` must stay green (`npx -y
  @fission-ai/openspec@latest validate --specs`); its parser reads only the
  first line of a requirement's text for the SHALL/MUST check, so lead with it.

## Security-review coverage

Two distinct concerns, kept separate:

- **The reviewer's own hardening** (so a malicious PR can't subvert *us*):
  prompt-injection defense with delimiter break-out neutralisation, broad secret
  redaction before egress, structured-output schema enforcement (`extra=forbid`
  rejects drifted/injected fields), and fork safety via `pull_request_target`
  with no checkout.
- **What the reviewer looks for** (so it catches issues in *your* PR): the system
  prompt (`engine/prompt.py`) carries an **OWASP-aligned security checklist** —
  injection, XSS, CSRF/open redirect, hardcoded secrets, broken authn/authz
  (incl. JWT/session pitfalls), path traversal, unrestricted file upload, SSRF,
  insecure deserialization/XXE, mass assignment, weak crypto, sensitive-data
  exposure (secrets/PII — passwords, tokens, SSNs, card data — leaking into
  logs), CI/IaC misconfiguration (workflow script injection, unpinned actions,
  broad IAM, public buckets, privileged containers), resource/DoS safety (incl.
  ReDoS) — graded `high`/`critical`. Alongside security it also scans for
  **correctness/logic bugs** (edge cases, null/None derefs, off-by-one and
  boundary errors, mismatched/inverted ranges, unhandled error paths, races /
  TOCTOU / async mistakes, numeric and date/time bugs, aliasing & mutation;
  "Correctness & logic" section), **missing or weak tests** for changed code
  paths (flagged `low`/`medium`, with a runnable test in the finding's
  `suggestion` field; weak = assertion-free / over-mocked / sleep-based; "Test
  coverage" section), **documentation gaps and stale docs** on public APIs
  (`info`/`low`, up to `medium` for a docstring/comment the change made wrong;
  "Documentation" section), **performance regressions** (N+1 queries,
  accidentally quadratic work, redundant computation, hot-path
  allocations/blocking I/O, unbounded queries, caches without eviction; graded by
  impact up to `high`; "Performance" section), needless **complexity** (deep
  nesting / high cyclomatic complexity, over-long low-cohesion functions,
  duplicated logic, dead code; `info`/`medium`, restrained; "Complexity"
  section), **intent mismatches** (out-of-scope hunks, contradictions,
  unfulfilled claims vs the stated intent; `medium`/`high`; "Intent" section),
  and **needless code** (YAGNI / speculative generality, reinventing the stdlib,
  code that could be far shorter, premature configurability; `info`/`medium`,
  restrained; the Ponytail "lazy senior dev" lens, "Ponytail" section).

Both are covered by tests in `tests/engine/` (`test_redact.py`, `test_injection.py`,
`test_prompt.py`, `test_parse.py`, `test_engine.py`) and `tests/github/test_diff.py`.
When you touch redaction, injection, the prompt, or the skip filter, extend those
suites — a security change without a test is exactly what CI rejects.

The reviewer also flags **deprecated APIs and end-of-life / vulnerable
dependencies** in the PRs it reviews (prompt section "Deprecation & dependency
health"; covered by `test_prompt.py`). Every scan category is asserted in
`test_prompt.py` (`test_prompt_asks_for_logic_and_edge_case_review`,
`test_prompt_asks_for_test_coverage`, `test_prompt_asks_for_documentation_review`,
`test_prompt_names_pii_and_secrets_in_logs`, `test_prompt_asks_for_performance_review`,
`test_prompt_asks_for_complexity_review`, `test_prompt_asks_for_intent_review`,
`test_prompt_asks_for_ponytail_review`,
plus the topic-coverage block: concurrency/races, numeric/datetime, CSRF /
redirect / XXE / mass assignment, CI/IaC, weak tests, stale docs, leaks,
typosquats) — extend those when you change the prompt's checklist. Prompt
mechanics are guarded too: every focused prompt carries exactly one
category-matched worked example with a real hunk header, the contract explains
the `line`/`side` arithmetic, and the injection wrapper's task restatement must
match the `{"findings": []}` object shape (`test_injection.py`).

## Code-quality & dependency hygiene

Split by whether it can be deterministic, because that decides where it lives:

- **Deterministic → per-PR gate.** Deprecated-API use is a hard error
  (`filterwarnings = error::DeprecationWarning` in `pyproject.toml`;
  `tests/test_code_quality.py` also imports every module under that filter and
  asserts the gate stays wired). Lockfile drift is caught by `uv lock --check`
  in CI. Outdated *syntax* is caught by ruff's `UP` rules. Don't weaken the
  deprecation gate to silence third-party noise — add a narrow per-library
  `ignore` instead.
- **Not deterministic → background/scheduled.** "Is a newer version available?"
  and "does a dep have a known CVE?" depend on what's published upstream at
  check-time, so they can't be a reproducible gate. They run on a schedule:
  `.github/dependabot.yml` (weekly grouped update PRs for the `uv` + GitHub
  Actions ecosystems, plus security-update PRs) and `.github/workflows/audit.yml`
  (`pip-audit` on the locked runtime deps — weekly cron + on dependency-touching
  pushes/PRs, never a blanket per-PR gate so an upstream CVE can't break an
  unrelated build).
- **Model quality → on-demand eval harness.** "Does this model/setting actually
  produce usable reviews?" needs a live model, so it can't be in the pytest gate.
  `evals/` (`run.py` + `scorer.py` over `evals/fixtures/`) reviews each fixture
  with a real provider and reports **parse-rate + recall + a clean / false-positive
  check**, exiting non-zero below `--min-recall` so it can gate a model/prompt
  change when run deliberately
  (`python -m evals.run --provider … --model …`; `--timeout` / `--num-ctx` /
  `--max-input-tokens` tune it for a big diff on a slow local model;
  `--temperature` / `--top-p` / `--top-k` set the model's sampling; `--categories`
  cuts the per-category fan-out to a subset). Its plumbing
  is unit-tested in `tests/evals/`. The **hosted** providers stay out of the pytest
  gate, and so does the live ollama path — the full lens set
  and the large multi-file `vibe-multifile` fixture stay in-repo for on-demand
  `python -m evals.run` runs: the fixtures plant security + correctness bugs **and**
  blatant performance (N+1 / quadratic) + complexity (deep nesting / duplication)
  issues so a full run exercises those code lenses, with the per-lens coverage
  guarded in `tests/evals/test_fixtures.py`. (Two lenses aren't scored there: the
  intent lens needs a stated intent the fixtures don't carry, and the ponytail
  lens looks for needless code the fixtures don't plant — the engine still runs
  ponytail, but there's no planted finding for it to match.) Beyond recall, a
  fixture can declare **`forbidden`** findings — claims that must *not* appear,
  typically cross-file false positives where the relevant guard lives in an unshown
  file; any produced finding matching one is a **false positive** that makes the
  fixture un-**clean** and fails the run. `_gate` therefore has **three bars**:
  parse, pooled recall, and clean. The **`cross-file-fp`** fixture is the worked
  example — one genuine in-diff catch (a logged secret) plus three forbidden
  cross-file traps (model_dump-vs-V2, idempotency re-run, tenant_id null) — and it
  measures the codebase-humility behavior the review prompt + reflection enforce.
  **`unshown-code-fp`** is its sibling for the two shapes cross-file-fp cannot
  express (its changed file is new, so nothing of it is unshown): a short edit at
  the bottom of a long existing module traps a claim about **what an unshown
  constant in the SAME file contains** (an empty frozenset ~180 lines up, the
  `_SINGLE_STREAM_PROVIDERS` shape) and a claim that the change is **untested**
  when the test lives in a file the diff never touched — the shape reflection's
  gap-finding carve-out currently protects. Real-spend hosted-provider e2e
  remains label-gated in `action-e2e.yml`.

[litellm]: https://github.com/BerriAI/litellm
