# Product spec: CLI configuration, simplified

**Status:** proposed · **Owner:** Matt Coles · **Scope:** local CLI UX only —
no engine behavior changes, no flag removals.

## The problem

lgtmaybe's promise is *"one flag, no keys in secrets, get a review."* The
engine delivers that. The CLI configuration surface doesn't:

- `lgtmaybe review --help` prints **31 options**. Roughly eight of them are
  what a user actually reaches for (`--provider`, `--model`, `--base`,
  `--working`, `--uncommitted`, `--format`, `--min-severity`, `--full`). The
  other twenty-three are engine tuning — `--num-ctx`, `--prompt-cache`,
  `--symbol-resolution`, `--structured-output`, `--unanchored-min-severity`,
  `--max-input-tokens` — that first-time users must scroll past to find the
  basics. The help screen *is* the product for a CLI; ours reads like the
  engine's internals manifest.
- `lgtmaybe config set` accepts all **35 `ReviewConfig` fields** as flat keys.
  A typo'd key dumps the full 35-key list — including keys like
  `extra_lenses` and `finding_rules` that can't meaningfully be set from a
  one-line string anyway.
- `lgtmaybe config init` asks three raw prompts with defaults
  (`ollama` / `llama3`) that are stale and fail silently later: it never
  checks that ollama is running, that the model is pulled, or that a typed
  provider name is even valid. The first failure the user sees is a review
  run erroring minutes later.
- The built-in default (`provider=ollama, model=llama3`) means the zero-config
  first run fails for most users — and `llama3` is no longer a model anyone
  would choose.

**The underlying need:** users don't want to *configure* a reviewer; they want
to *be configured* — pick a provider once, trust the defaults, and only meet a
knob at the moment they need it.

## Design principle

**Great defaults, progressive disclosure.** Every option stays available
(nothing breaks), but the default experience shows only what the current user
needs. Tuning knobs earn their visibility when the user signals intent
(`--help-all`, an "Advanced" section, a doctor diagnosis).

## Personas

1. **First-run local dev** — installed via pip/brew, wants findings on their
   branch in under two minutes. Touches: `config init`, `review`.
2. **CI adopter** — wires the GitHub Action; configures via `action.yml`
   inputs and `.lgtmaybe.yml`. Mostly out of scope here (the Action surface is
   fine), but benefits from the same key curation in docs.
3. **Power tuner** — slow local model, custom endpoint, eval-driven. Needs
   every existing knob; must lose nothing.

## Proposals (prioritized)

### P0-1 — Progressive-disclosure help

Group `review`'s options into **Common** (~8, shown by default) and
**Advanced** (everything else, shown with `lgtmaybe review --help-all` /
`lgtmaybe help review --all`, and summarized by one trailing line in the short
help: *"23 advanced tuning options: run `lgtmaybe review --help-all`"*).

- Common: `--provider`, `--model`, `--base`, `--working`, `--uncommitted`,
  `--format` / `--json`, `--min-severity`, `--full`, `--config`.
- Advanced: model-call tuning (`--fallback-model`, `--reflect-model`,
  `--triage-model`, `--temperature`, `--timeout`, `--max-review-seconds`,
  `--max-concurrency`, `--max-input-tokens`, `--num-ctx`), pipeline toggles
  (`--reflect`, `--recursive`, `--structured-output`, `--symbol-resolution`,
  `--prompt-cache`, `--static-analysis`), filtering
  (`--unanchored-min-severity`, `--min-confidence`, `--max-files`,
  `--context-lines`), diagnostics (`--profile`).
- Every flag keeps working exactly as today — this changes *help rendering
  only*.

**Acceptance:** default `--help` fits one terminal screen (~40 lines);
`--help-all` shows the full reference; a hidden option still parses.

### P0-2 — `config init` detects instead of asks

Turn the three raw prompts into a short wizard that observes the environment
first and confirms rather than interrogates:

1. **Detect** credentials and local servers: provider env keys present
   (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
   `ZAI_API_KEY`), ambient AWS/GCP/Azure creds, and a running ollama
   (`GET /api/tags` — which also yields the pulled model list).
2. **Offer** what was found as the default choice ("Found
   `ANTHROPIC_API_KEY` — use anthropic? [Y/n]"), with a numbered provider list
   as fallback. Validate the provider name against the real enum instead of
   accepting any string.
3. **Suggest current models** per provider (a small, maintained table — same
   convention as the docs' "model IDs kept current" rule) instead of the
   hardcoded `llama3`.
4. **Verify** with one tiny completion call before writing the file
   (skippable with `--no-verify`), so a bad key/model fails *now*, with the
   provider's "how to auth" message, not mid-review later.
5. Finish by printing the file path and the exact next command:
   `lgtmaybe review`.

**Acceptance:** on a machine with a running ollama, `config init` completes
with two keypresses; a wrong API key fails during init, not during review.

### P0-3 — Retire the `llama3` built-in default

With no config at all, `lgtmaybe review` should not assume `ollama/llama3`.
Instead fail fast with a two-line pointer: *"No provider configured — run
`lgtmaybe config init` (60 seconds), or pass `--provider`/`--model`."* A
wrong-guess default that errors deep in the run is worse than an honest,
actionable one-liner up front.

**Acceptance:** fresh install + no config + `lgtmaybe review` exits non-zero
in under a second with the pointer; `config init` then makes the same command
succeed.

### P1-1 — Curate `config set` keys

Split the 35 flat keys into **everyday** (settable directly: `provider`,
`model`, `api_base`, `preset`, `min_severity`, `max_files`, `timeout`,
`temperature`, `reflect`, `min_confidence`) and **advanced** (everything else
— still settable, but `config set <advanced-key>` notes it's an advanced
knob and links the reference). Structured keys that can't be expressed as one
string (`extra_lenses`, `finding_rules`, `static_analysis`, `categories`)
get a purpose-built error pointing at `.lgtmaybe.yml` and the how-to doc,
instead of a coercion traceback.

Unknown keys get **did-you-mean** (closest-match against valid keys) instead
of the 35-key dump.

**Acceptance:** `config set modle x` suggests `model`; `config set
extra_lenses …` explains the YAML path; every currently-valid key still
persists.

### P1-2 — `lgtmaybe doctor`

One command that answers "why doesn't it work": prints resolved config (with
which layer each value came from — built-in / user / repo / flag), whether
credentials resolve for the chosen provider, whether the endpoint is
reachable (ollama tags / api_base HEAD), and whether the model is known.
This is where hidden complexity *should* live — invisible until something
breaks, then comprehensive.

**Acceptance:** with a stopped ollama, `doctor` names the connection failure
and the fix; with a healthy setup it prints all-green in <5s.

### P2-1 — `config show` explains itself

`config show` today prints the raw YAML (or nothing). Add the resolved
effective config with layer provenance (`model: claude-sonnet-5  # repo
.lgtmaybe.yml`) and a `--defaults` flag that includes the built-ins users
never set. Turns "what will actually run?" from archaeology into one command.

### P2-2 — Docs follow the same split

`docs/reference/config.md` (generated) gains the same Common/Advanced
grouping so the reference reads in the same order as `--help`. Keep the
generation-from-models pipeline; only the ordering/grouping metadata is new.

## Non-goals (deliberate)

- **No flag or key removals/renames.** Everything shipped stays; this is
  presentation and onboarding, not surface reduction. No deprecation churn.
- **No new config layers or file formats.** The precedence chain
  (flags → repo → user → defaults) is right; we make it visible, not bigger.
- **No persisted secrets.** `config init`'s verification call reads keys from
  the environment exactly as today; the never-persist rule is untouched.
- **Action inputs unchanged.** CI users configure via YAML they read once;
  the pain is interactive-CLI-shaped.

## Success measures

- Time-to-first-successful-review on a fresh machine: **< 2 minutes** (today:
  unbounded — the default config fails and the user reads docs).
- `lgtmaybe review --help` fits one screen without scrolling.
- Config-shaped GitHub issues ("how do I…", "why does it use llama3") trend
  to zero.

## Suggested sequencing

P0-1 and P0-3 are small, independent, and land the biggest first-impression
win; P0-2 is a contained feature behind an existing command. P1s follow once
the everyday-vs-advanced key split from P0-1 exists to reuse. Each item ships
with its acceptance test first, per the repo's TDD gate.
