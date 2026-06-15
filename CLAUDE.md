# CLAUDE.md

Guidance for agents working in **lgtmaybe** — a provider-agnostic PR reviewer.
Read this before writing code. It encodes decisions that are **made, not options**.

## What this is

A PR reviewer that posts inline review comments + a summary. The user picks the
LLM backend with a `--provider` flag, drops a key into GitHub secrets (or wires
OIDC/WIF for cloud providers), and gets a review. One core, two distribution
variants:

- **PyPI CLI** — `pip install lgtmaybe`
- **GitHub Action** — composite action (`action.yml`) that does keyless OIDC/WIF
  auth, then runs a GHCR image via the `action` entrypoint

**The wedge:** first-class **Bedrock + Vertex + Azure with keyless OIDC/WIF**.
Six hosted providers (plus local ollama), one flag, no keys in secrets for
cloud. We win on auth + simplicity.

## Non-negotiables

- **TDD, always: red → green → refactor.** Write the acceptance test from a
  task's stated in/out *first*, watch it fail, write the minimum code to pass,
  then refactor. CI rejects a PR whose diff adds code without a test.
- **Structured output only.** The model returns JSON (`severity`, `file`,
  `line`, `body`, `suggestion`). Never parse prose.
- **Fork safety.** Trigger on `pull_request_target` so the review has secrets,
  but **never check out or execute PR code** — fetch the diff via API only.
  Treat all diff content as untrusted input.
- **No static cloud keys.** Bedrock uses ambient AWS creds; Vertex uses ambient
  GCP creds; Azure prefers ambient Entra (Azure AD) creds via GitHub OIDC (a
  static `AZURE_API_KEY` is accepted but not required). Never accept or require a
  service-account JSON or static AWS key.

## Key decisions (do not relitigate)

- **Language:** Python.
- **Provider spine:** [litellm] — normalises openai, openrouter, anthropic,
  bedrock, vertex, azure, ollama to one `completion()` call. A thin wrapper on
  top adds retries / fallback.
- **License:** MIT (already in `LICENSE`).
- **Posting:** REST review API — batched inline comments + one summary.
  Idempotent updates via a hidden marker comment.

### Auth model — resolved by provider (chain of responsibility)

| Provider               | Auth                                                              |
|------------------------|------------------------------------------------------------------|
| openai / openrouter / anthropic | API key from `secrets.*` / env / `--api-key`            |
| bedrock                | ambient AWS creds (GitHub OIDC role, or local `~/.aws`); IAM `bedrock:InvokeModel*` only |
| vertex                 | ambient GCP creds (WIF, or local ADC)                            |
| azure                  | needs the resource endpoint (`--api-base` / `AZURE_API_BASE`); ambient Entra creds (GitHub OIDC federation via `azure/login`, or local `az login` / managed identity) → else `AZURE_API_KEY` / `--api-key` |
| ollama                 | none — just an `api_base` (localhost, host.docker.internal, tailscale host); fully local, zero cost |

Resolver order: chosen provider → try ambient cloud creds if that's its native
mode → else API key → ollama needs neither → else **fail with a clear "how to
auth this provider" message**.

## Architecture — ports & adapters (hexagonal)

This is what lets tracks build in parallel against frozen contracts.

- `core/ports.py` — the ports (interfaces). **Frozen in the foundation step.**
- litellm / github classes — the adapters.
- **Engine is a pipeline:** `fetch → compress → prompt → parse → merge/dedupe →
  reflect → filter → post`, as composable stages. The prompt/parse stage **fans
  out per `ReviewCategory`** — one concurrent model call per lens — then merges +
  de-dupes the findings, and a **self-reflection pass** (`engine/reflect.py`)
  drops the model's own low-confidence findings before posting.
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
     `/ask <q>` + `/describe` reply in-thread (`post_issue_comment`, an
     adapter-only method beyond the frozen port).
   - **Guards (in the engine):** generated/binary files skipped via
     `is_reviewable`; **file cap** reviews the top-N and posts a "reviewed top N
     of M" notice.
   - **Context expansion:** `get_pr_context` also fetches the head text of
     reviewable files via the API (read-only, never a checkout) into
     `PRContext.file_contents`; the engine (`compress.expand_hunks`) pads each
     hunk with budget-scaled surrounding lines, capped by
     `ReviewConfig.context_lines` (default 20, `0` disables), redacted like the
     diff. Inline positions stay bound to the **real** diff, so a finding on a
     context-only line maps to nothing and is dropped — never mis-posted.
   - **Error surfacing:** any failure posts a short "review failed" comment and
     the CLI exits non-zero (`ClickException`) — never fails silently.
   - **Per-category fan-out:** the system prompt is composed per `ReviewCategory`
     (security, correctness, deprecation, tests, documentation, performance,
     complexity, intent; `engine/prompt.py`) — each lens gets its **own worked
     example** (with a real hunk header, teaching the line-number arithmetic) —
     and the engine runs each category as its own **concurrent** `provider.complete`
     call per batch (a `ThreadPoolExecutor` over the sync port — concurrent for
     cloud, serial for ollama), then **merges and de-dupes** the findings
     (`engine._dedupe`, keyed on path/line/side/title) before reflection.
     `ReviewConfig.categories` selects the lenses (default: all eight).
   - **Custom lenses (BYO):** beyond the built-in `ReviewCategory` set, users add
     their own lenses via `ReviewConfig.extra_lenses` (a `CustomLens`: `id` +
     `instructions`, optional `title` and a worked `example_diff`/`example_finding`)
     — defined inline in `.lgtmaybe.yml` or in skill files loaded by the config
     loader's `lens_paths` directive. The engine builds a uniform `_Lens` per
     built-in category **and** per custom lens (`engine._build_lenses`,
     `prompt.build_lens_prompt`) and fans them all out identically through the same
     merge/dedupe/reflect pipeline. Lens text enters the system prompt, so it is
     **trusted config only** — never sourced from PR-author content (on
     `pull_request_target` config comes from the base, not the PR head). Covered by
     `tests/engine/test_prompt.py`, `tests/engine/test_engine.py`,
     `tests/config/test_loader.py`, and `tests/test_models.py`.
   - **Intent lens:** "does the PR do what it says?" — `PRContext` carries the
     stated intent (`title`, `description`, `commit_messages`): PR title/body +
     commit names via the REST gateway, or `git log` commit names from the local
     CLI (`local/_commit_subjects`), so it works without GitHub. The engine
     redacts the intent text, wraps it via `injection.wrap_intent` (its own
     neutralised `INTENT_START`/`INTENT_END` block), and sends it **only on the
     intent call**; with no stated intent the lens is skipped (logged, no notice).
   - **Self-reflection:** after merge/dedupe, `engine/reflect.py` asks the
     provider to audit its own findings for false positives and drops the ones it
     marks low-confidence. The verdict is structured (`ReflectionResult` —
     `{"verdicts": [{"index", "keep"}]}`) with a lenient parser and a **keep-all
     safe default** when it can't be parsed (never silently drop a real finding).
     Skippable via `--no-reflect` for weaker models that over-prune.
   - **Determinism & timeouts:** `temperature` defaults to `0.0` for reproducible
     reviews; `timeout` is `None` → a provider-aware default (ollama gets a long
     one, cloud a short one). Both are `ReviewConfig` fields and CLI/Action inputs.
   - **Summary line:** names the **model** used (no cost — lgtmaybe does not
     compute or report cost).
   - **Clean review:** zero findings on a fully-reviewed PR posts `👍 LGTM!`
     (comment only — no GitHub approval state) — still naming the model.
4. **Packaging (sequential, last) — DONE:** the two distribution variants over
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
  section), and **intent mismatches** (out-of-scope hunks, contradictions,
  unfulfilled claims vs the stated intent; `medium`/`high`; "Intent" section).

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
  with a real provider and reports **parse-rate + recall**, exiting non-zero below
  `--min-recall` so it can gate a model/prompt change when run deliberately
  (`python -m evals.run --provider … --model …`; `--timeout` / `--num-ctx` /
  `--max-input-tokens` tune it for a big diff on a slow local model;
  `--temperature` / `--top-p` / `--top-k` set the model's sampling; `--categories`
  cuts the per-category fan-out to a subset). Its plumbing
  is unit-tested in `tests/evals/`. The **hosted** providers stay out of the pytest
  gate, but a real **local ollama** run *is* wired into CI as its own workflow —
  `.github/workflows/e2e-ollama.yml` is a deliberately **cut-down smoke**: it pulls
  a small model (`qwen3.5:2b`, thinking forced off) and reviews the single small
  `badcode` fixture through only the two **critical** lenses (`--categories
  security,correctness`) on every PR with `--no-reflect` (the reflection pass
  over-prunes on a small model) and qwen3.x's recommended non-thinking sampling
  (`--temperature 0.6 --top-p 0.8 --top-k 20`). It proves the live ollama path
  parses and picks up critical issues — not that a small model is thorough — so the
  floor is low (`--min-recall 0.2`, pooled across fixtures — every fixture must
  parse, and total caught / total planted must clear the floor). The full lens set
  and the large multi-file `vibe-multifile` fixture stay in-repo for on-demand
  `python -m evals.run` runs: the fixtures plant security + correctness bugs **and**
  blatant performance (N+1 / quadratic) + complexity (deep nesting / duplication)
  issues so a full run exercises all seven code lenses, with the per-lens coverage
  guarded in `tests/evals/test_fixtures.py`. (The eighth lens, intent, needs a
  stated intent the fixtures don't carry, so the engine skips it there by design.)
  Real-spend hosted-provider e2e remains label-gated in `action-e2e.yml`.

[litellm]: https://github.com/BerriAI/litellm
