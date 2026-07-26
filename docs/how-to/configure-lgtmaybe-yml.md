---
description: Configure lgtmaybe with a .lgtmaybe.yml file — provider, model, severity floor, lenses, caps, and other non-secret defaults.
---

# Configure .lgtmaybe.yml

Place a `.lgtmaybe.yml` file at the root of your repository to control how
lgtmaybe reviews pull requests. CLI flags override file values; the file
provides defaults for all runs.

## Contents

- [Full example](#full-example)
- [Field reference](#field-reference)
  - [provider](#provider)
  - [model](#model)
  - [min_severity](#min_severity)
  - [include_paths / exclude_paths](#include_paths-exclude_paths)
  - [max_files](#max_files)
  - [max_input_tokens](#max_input_tokens)
  - [preset](#preset)
  - [categories](#categories)
  - [context_lines](#context_lines)
  - [function_context](#function_context)
  - [timeout](#timeout)
  - [structured_output](#structured_output)
  - [prompt_cache](#prompt_cache)
  - [reflect](#reflect)
  - [min_confidence](#min_confidence)
  - [incremental](#incremental)
  - [static_analysis](#static_analysis)
  - [triage_model](#triage_model)
  - [auto_describe](#auto_describe)
  - [pr_labels](#pr_labels)
  - [finding_rules](#finding_rules)
  - [summary_template](#summary_template)
  - [resolve_fixed](#resolve_fixed)
  - [extra_lenses](#extra_lenses)
  - [lens_paths](#lens_paths)
- [CLI flag overrides](#cli-flag-overrides)

## Full example

```yaml
provider: openai
model: gpt-5.5
min_severity: low
include_paths:
  - "src/**"
  - "lib/**"
exclude_paths:
  - "**/__pycache__/**"
  - "**/*.min.js"
max_files: 30
max_input_tokens: 80000
categories:
  - security
  - correctness
  - tests
```

## Field reference

See [Reference: Config](../reference/config.md) for the full schema with
types and defaults.

### provider

Which LLM backend to use. One of `openai`, `openrouter`, `anthropic`, `zai`,
`bedrock`, `vertex`, `azure`, `ollama`, `openai-compatible`.

```yaml
provider: anthropic
```

### model

The model identifier for the chosen provider. Format varies by provider:

| Provider | Example model IDs |
|---|---|
| openai | `gpt-5.5` |
| anthropic | `claude-sonnet-4-6`, `claude-haiku-4-5` |
| openrouter | `anthropic/claude-sonnet-4-6` |
| zai | `glm-4.6`, `glm-4.7`, `glm-4.5-air` (GLM / Zhipu AI; newer `glm-5.x` pass through too) |
| bedrock | `us.anthropic.claude-sonnet-4-6`, `us.anthropic.claude-haiku-4-5` (prefer the cross-region inference profile; a non-Bedrock id like `openai.gpt-5.5` is invalid — see [Review with Bedrock](review-with-bedrock-oidc.md)) |
| vertex | `gemini-3-pro`, `gemini-3.5-flash` |
| azure | your deployment name, e.g. `my-gpt-4o-deployment` (not the upstream model id — see [Review with Azure](review-with-azure.md)) |
| ollama | `qwen3.6:27b`, `gemma4:e4b` |
| openai-compatible | the served model name, e.g. `deepseek-chat` or `meta-llama/Llama-3.1-8B-Instruct` (requires `api_base` — see [Use a custom OpenAI-compatible endpoint](use-a-custom-openai-compatible-endpoint.md)) |

### min_severity

The minimum severity level to report. Findings below this threshold are
suppressed. Ordered low to high: `info`, `low`, `medium`, `high`, `critical`.

```yaml
min_severity: medium   # suppresses info and low findings
```

Default: `low` (suppresses only `info` findings).

### include_paths / exclude_paths

Glob patterns to restrict which files in the diff are reviewed.
`include_paths` acts as an allowlist; `exclude_paths` acts as a denylist applied
after the allowlist, so an exclude always wins. Both default to empty (all files
included). Patterns match against the full repo-relative path, and a
`**/`-prefixed pattern also matches at the repo root (so `**/*.lock` covers a
root-level lockfile). The built-in skip filter for generated, vendored, and
binary files runs first either way — an `include_paths` entry can't resurrect a
lockfile.

```yaml
include_paths:
  - "src/**"
exclude_paths:
  - "src/generated/**"
  - "**/*.lock"
```

### max_files

Maximum number of changed files to include in the review. Files beyond this
limit are skipped. Reduces token usage on large PRs.

```yaml
max_files: 30
```

Default: `50`.

### max_input_tokens

Token budget **per model call**. When the compressed diff exceeds this limit,
lgtmaybe splits it across multiple batched calls (and, with `recursive` on,
walks an over-budget single file hunk-by-hunk) — nothing is truncated or
dropped.

```yaml
max_input_tokens: 80000
```

Default: `100000`.

### preset

How many model calls the review spends. `fast` (the default) covers all nine
categories in **four calls**, one per concern:

| Call | Covers |
|---|---|
| security | security |
| correctness | correctness, and stated intent when the PR states one |
| code health | performance, complexity, ponytail, deprecation |
| artefacts | tests, documentation |

The same four run on every provider — worker count changes only how they are
scheduled, not how many there are. `full` runs one focused call per lens for
release branches and deep audits.

```yaml
preset: full
```

Default: `fast`. CLI: `--preset fast|full` (`--full` is shorthand). An explicit
`categories` list (below) overrides the preset grouping.

### categories

Which review lenses to run. An explicit list disables the preset grouping: the
reviewer asks for each listed category in its own concurrent model call and
merges the findings, so a focused prompt concentrates on one concern at a
time. One or more of `security`, `correctness`, `deprecation`, `tests`,
`documentation`, `performance`, `complexity`, `intent`, `ponytail`. Narrowing
the list trades thoroughness for fewer model calls (and lower token usage).

The `ponytail` lens is the "lazy senior dev" check — *the best code is the code
you never wrote* — flagging code that needn't exist at all (YAGNI, reach for the
standard library, do it in fewer lines). See
[What gets reviewed](../explanation/what-gets-reviewed.md#ponytail-the-laziest-senior-dev-in-the-room).

The `intent` lens checks the diff against the PR's stated intent — title,
description, and commit names on GitHub; your `git log` commit names on the
CLI (in both branch and `--working` mode). When nothing states an intent (e.g.
no commits beyond the base branch yet), it is skipped automatically, so it
never costs an extra call (under the `fast` preset it shares correctness's
call). It is also the only lens that sends the PR title/description/commit
names to the provider — drop it from `categories` if you don't want that text
sent at all.

```yaml
categories:
  - security
  - correctness
```

Default: all nine categories.

### context_lines

Ceiling on the number of unchanged lines added around each changed hunk. The
lines are read from the head revision of the file, so the model reviews a
change in the context of its surrounding code. The pad is **asymmetric**: the full budget goes
before the hunk (the enclosing signature and setup explain a change best) and a
quarter of it — at least one line — goes after. The actual number used is the
smaller of this ceiling and what the token budget allows, so it shrinks
automatically on large PRs. Set it to `0` to disable context expansion and
review the bare diff (no extra file content is fetched).

```yaml
context_lines: 10   # at most 10 lines before each hunk (2 after); 0 disables
```

Default: `20`.

### function_context

Extend each hunk's leading pad up to the **enclosing function or class
signature** when it sits above the fixed `context_lines` window — the
signature and setup explain a change better than an arbitrary cut. Boundaries
are found structurally with ast-grep (already bundled for symbol resolution;
parsing only, never executing) for Python, JS/TS/TSX, Go, Rust, Java, and
Ruby, with a bounded reach so a distant definition can't drown the diff.
Unsupported languages and any ast-grep failure keep the plain fixed-line pad.

```yaml
function_context: false   # fixed-line padding only
```

Default: `true`.

### timeout

Per-request timeout in seconds for each model call. Left unset, lgtmaybe picks a
**provider-aware default**: **1800 s for ollama, openai-compatible, and
openrouter** (local models are slow, and openrouter can route to slow reasoning
models) and 600 s for direct cloud providers. Set it explicitly to raise it for a
large local model.

```yaml
timeout: 3600   # an hour per call, e.g. for a big model on CPU
```

Default: auto (ollama/openai-compatible/openrouter 1800 s, cloud 600 s). See
[Run locally with ollama](run-locally-with-ollama.md#slow-models-and-timeouts).

### structured_output

Constrain the model to emit the findings JSON schema using the provider's native
JSON mode (litellm `response_format`). This keeps models — especially local ones —
from returning prose or reasoning instead of findings. Leave it on unless a
particular model/provider **rejects** `response_format` (some `openai-compatible`
gateways return a `400`), in which case turn it off; the lenient parser still
strips fences and pulls JSON out of any surrounding prose. CLI: `--no-structured-output`.

```yaml
structured_output: false   # only if your gateway rejects JSON-schema mode
```

Default: `true`. See
[Use a custom OpenAI-compatible endpoint](use-a-custom-openai-compatible-endpoint.md#gateways-that-dont-support-json-mode-response_format).

### prompt_cache

Reuse the expensive shared prefix — system preamble plus the wrapped diff —
across the per-lens review calls and the reflection call, instead of re-paying
full input price for it on every one. lgtmaybe fans out several model calls per
review and they all begin with that same prefix, so it shapes every call
identically and lets the backend serve it from cache.

Two mechanisms, picked per route:

| Route | How it caches |
|---|---|
| **anthropic**, **bedrock** Claude/Nova, **vertex** (Claude and Gemini), **zai** GLM, **openrouter** (claude / gemini / glm / minimax / z-ai models) | lgtmaybe marks the prefix with an explicit `cache_control` breakpoint |
| **openai**, **azure**, **deepseek** (direct or via openrouter) | the backend caches a repeated prefix automatically — the identical shape is all it needs |
| **ollama**, `openai-compatible` | the request is sent unchanged |

Support is feature-detected per model, so a route in the first row whose model
litellm doesn't know about simply falls back to the second behaviour — a missed
discount, never an error. The diff-independent parts of the prompt are what get
reused; per-PR content still enters the prefix, which is why it is only ever
shared *within* one review.

On a large diff lgtmaybe also runs a **warm-up primer**: the first lens of a
batch is dispatched alone and the rest release when it returns, so a fully
concurrent first wave doesn't all miss the cache (and, on breakpoint routes, all
pay the cache-write surcharge). This applies on every provider — it is about the
shape of the first wave, not about the marker.

Every call also carries a `prompt_cache_key` derived from the prefix itself:
identical across the lenses of one batch, different for another PR. OpenRouter
uses it to pin the whole fan-out to a single provider endpoint from the first
call (without a key it only starts doing that *after* it notices a cache hit,
which a concurrent wave reaches too late), and OpenAI takes the same field as a
cache-routing hint. It is a digest, not prompt content.

**Minimums are per model**, and a prefix below one is silently not cached — no
error, just no discount. Roughly: 1,024 tokens for Claude Sonnet 4.x / Opus
4–4.1 and Gemini 2.5 Flash, 2,048 for Claude Haiku 3.5, 4,096 for Claude Opus
4.5+ / Haiku 4.5 and Gemini 2.5 Pro. lgtmaybe marks from 1,024 up so it never
misses a chance on the lower-minimum models; on a higher-minimum model a small
diff simply won't cache. Check with `--profile`, which reports cache read and
write tokens.

Leaving it on costs nothing. Turn it off only to rule caching out while
debugging provider behaviour. CLI: `--no-prompt-cache`.

```yaml
prompt_cache: false   # send every call uncached, and never warm the batch
```

Default: `true`.

### reflect

Run the **self-reflection pass** that audits the merged findings and drops the
ones the model marks low-confidence, before anything is posted. This trims false
positives, so leave it on for most models. Turn it **off** for a weaker or local
model that over-prunes and drops valid findings during the audit. CLI:
`--no-reflect`.

```yaml
reflect: false   # keep every finding; skip the false-positive audit
```

Default: `true`. To audit a weak reviewer's findings with a stronger model
instead of disabling the pass, set `reflect_model` to that model id (it uses the
same provider and credentials as `model`).

### min_confidence

During reflection the auditor also scores each kept finding's confidence from
0 (certainly a false positive) to 10 (certain it is real), reached by actively
trying to disprove the finding against the diff and the file text. Findings
scored **below** `min_confidence` are dropped before posting; the surviving
score is shown in the CLI output and the JSON export. A finding the auditor
keeps but doesn't score always survives the threshold — a missing score never
drops a real finding. CLI: `--min-confidence`.

```yaml
min_confidence: 5   # drop findings the auditor scores 0-4
```

Default: `0` (no numeric filtering — reflection prunes only via its keep/drop
verdicts, as before the score existed).

### incremental

Commit-scoped incremental review, for the GitHub posting path. On a re-run
lgtmaybe reads a hidden watermark (the head SHA its last completed review
covered) from its own summary comment and reviews **only the diff of the
commits pushed since**, instead of the whole PR — faster, cheaper, and no
re-noise on code that was already reviewed. New findings post as inline
comments; findings on files outside the increment stay open, and are only
auto-resolved by a run that actually re-reviewed their file.

It always degrades to a **full review** when there is no watermark yet (first
review), after a force-push/rebase (the increment would be meaningless), or if
the compare fails. A failed review never moves the watermark, so no commit is
ever silently skipped. Comment `/review full` on the PR to force a full
re-review on demand.

```yaml
incremental: false   # every run reviews the whole PR
```

Default: auto — incremental on a `synchronize` push (new commits on an
already-reviewed PR), full review everywhere else (open/reopen, slash
commands, and the local CLI, which never uses it).

### static_analysis

Static-analysis fusion: run fast, deterministic tools over the changed files.
Each tool reaches the review in one of two **modes**:

- **`hint`** — findings become **hints for the model to confirm, contextualise,
  or discard**. This raises recall on exactly the mechanical bugs LLMs miss
  without posting raw linter noise; only findings the model itself confirms are
  reported. The default for **ruff**, **bandit**, **mypy** and **semgrep**.
- **`finding`** — findings are posted directly, with **no model call at all**.
  The default for **gitleaks** and **zizmor**. Deterministic, free, and
  identical run to run.

The split is about the tool, not taste: a committed credential is present or it
isn't, so asking a model to "confirm or discard" a regex match only adds latency
and a chance of it talking itself out of a real hit. A lint or a SAST heuristic
is the opposite — often technically true and beside the point — which is exactly
what the model is good at filtering. Override either way with `tool_mode`.

Supported tools: **ruff**, **bandit**, and **mypy** (Python), **gitleaks**
(secrets, any language), **zizmor** (GitHub Actions workflow security — template
injection, unpinned `uses`, over-broad permissions; it runs only when the PR
changes a workflow file), and **semgrep** (multi-language) when you point
`semgrep_rules` at local rules — semgrep's registry configs need the network,
which the sandbox forbids.

Two rules keep direct posting honest. Tools read **whole files**, but only the
diff is under review, so a finding on a line this PR did not change is dropped
and counted in the summary — otherwise a fake credential in a test fixture would
post on every PR that touches that file, forever. And direct findings are capped
per review, most severe first, since no model is there to filter volume.

Posted findings carry a `scan:<tool>` category, so `finding_rules` can drop or
re-grade a scanner without turning it off:

```yaml
finding_rules:
  - match: {category: "scan:gitleaks", path: "tests/fixtures/**"}
    action: drop
```

**mypy** earns its place on unguarded-`Optional` bugs: a `dict.get()` narrowed
to `str | None` and then dereferenced is a crash a review lens reads straight
past, and mypy proves it from the file's own text in seconds. It runs with
`--ignore-missing-imports --follow-imports=skip`, because the sandbox holds only
the changed files and everything they import is absent by construction —
so it reports what it can prove from a single file, and stays quiet about the
rest (untyped code and unresolvable imports produce nothing).

The tools run against the already-fetched file texts in a throwaway directory
(never a checkout, never executing PR code), in a subprocess with a scrubbed
environment (no proxy or credential variables) and a hard timeout. A tool that
isn't installed is skipped silently — install them with
`pip install lgtmaybe[static-analysis]`, or rely on whatever is already on
PATH. Tool output is treated as untrusted text: redacted and
injection-wrapped before it reaches the model. CLI:
`--static-analysis/--no-static-analysis`.

```yaml
static_analysis:
  enabled: true
  tools: [ruff, bandit, mypy, gitleaks, zizmor]  # default: all supported tools
  min_severity: low            # floor on mapped tool severity (default info)
  tool_min_severity:           # per-tool overrides of the global floor
    ruff: medium               # only medium+ from ruff; bandit keeps `low`
  tool_mode:                   # per-tool overrides of hint vs finding
    gitleaks: hint             # route secrets through the model instead
  # semgrep_rules: .semgrep.yml  # local rules; semgrep is skipped without them
```

Default: `enabled: false` — no subprocess ever runs and behaviour is
unchanged.

### triage_model

Two-stage model routing so routine PRs don't pay frontier prices while risky
ones still get the strong model. When set, this **cheap** model runs first
over the compressed per-file diffs. It skips files that plainly need no review
(pure formatting, trivial renames, generated churn) and scores the rest 0–10
by risk; the strong `model` then does the deep per-lens review only on the
survivors, riskiest first. Skipped files are listed in the review summary, and
`/review full` reviews everything on demand.

A deterministic **security floor** always escalates past triage, whatever the
cheap model says: security-relevant paths (auth/crypto/session code,
migrations, IaC, CI workflows, dependency manifests), patches carrying
security-relevant tokens, files with static-analysis hits, and large hunks.
Any triage failure — an unparseable verdict, a provider error — reviews
everything.

All three model slots (`triage_model`, `model`, `reflect_model`) resolve
through the same provider and credentials, so pointing them all at one ollama
model costs nothing. **Trade-off:** cheaper, faster reviews at the risk of the
triage model under-rating a subtle change; the floor and the
review-when-unsure prompt bound that risk, but for maximum recall leave triage
off. CLI: `--triage-model`.

```yaml
triage_model: claude-haiku-4-5   # cheap gatekeeper; unset = no triage
```

Default: unset (no triage — every file gets the full review, exactly as
before).

### auto_describe

Post a **structured PR description** as a comment when a PR is opened (or
reopened), before the review runs: a suggested title, the change type, a short
summary, a per-file walkthrough table, and — when the PR states an intent — a
"does it do what it says" check. The comment is updated **in place** by later
`/describe` runs, never duplicated, and a describe failure never blocks the
review. `/describe` posts the same structured description on demand whether or
not auto-describe is enabled.

```yaml
auto_describe: true
```

Default: `false`.

### pr_labels

Attach labels derived from the finished review — **no extra model calls**:

- `review-effort/1` … `review-effort/5` — a size estimate from the changed
  lines, so reviewers can gauge the PR at a glance;
- `possible-security-issue` — a high/critical finding from the security lens
  was posted;
- `consider-splitting` — the diff spans many unrelated top-level directories.

Labels are reconciled on each run (a stale `review-effort/2` is removed when
the score changes) and only lgtmaybe's own label families are ever touched.
Best-effort: a labelling failure never fails the review.

```yaml
pr_labels: true
```

Default: `false`.

### finding_rules

Declarative post-processing applied to findings just before posting — the
safe alternative to arbitrary post-processing hooks (rules can only filter or
re-grade; **no user code ever runs**). Each rule has a `match` (all specified
fields must match) and an `action`; rules apply in order.

Match fields: `path` (glob, `**/`-prefix also matches at the repo root),
`category` (the lens that produced the finding — `security`, `correctness`,
…, or a custom lens id), `title_contains` (case-insensitive substring), and
`min_severity` (at or above). Actions: `drop: true` or `set_severity`.

```yaml
finding_rules:
  # complexity nits in tests aren't worth a comment
  - match: {path: "tests/**", category: complexity}
    action: {drop: true}
  # documentation findings are informational for this repo
  - match: {category: documentation}
    action: {set_severity: info}
```

Default: no rules.

### summary_template

Custom template for the review summary line, for teams matching a house
style. Placeholders: `{count}` (findings posted), `{provider}`, `{model}`,
`{version}` (the lgtmaybe release that produced the review). A template that
fails to format falls back to the built-in line.

```yaml
summary_template: "🤖 {count} finding(s) · {model} · lgtmaybe {version}"
```

Keep `{version}` if you can: the same model on the same provider reviews
differently across releases, so it is the handle that makes a surprising review
traceable to the code that produced it.

Default: unset (the built-in
`N findings · provider X · model Y · lgtmaybe Z` line).

### resolve_fixed

Auto-resolve a review conversation once its finding is fixed. On a re-run, when a
finding lgtmaybe raised is no longer produced **and** GitHub marks that thread
outdated (the code under it changed), lgtmaybe posts a short `✅ Looks resolved.`
reply and resolves the conversation. Both conditions must hold, so a thread is
never collapsed just because nearby lines shifted. Set it to `false` to leave
conversations for manual resolution.

GitHub posting only — the local CLI review has no conversations to resolve, so it
ignores this. Resolving a thread uses GitHub's GraphQL API; the default
`GITHUB_TOKEN` (`pull-requests: write`, already needed to post the review) is
sufficient.

```yaml
resolve_fixed: false   # leave fixed conversations open for manual resolution
```

Default: `true`.

### extra_lenses

Define your own review lenses ("BYO skills") that run **alongside** the built-in
`categories`. Each one fans out as its own focused model call and its findings
merge into the same review. A lens needs an `id` (unique, and not one of the
built-in category names) and `instructions` describing what to look for; a
`title`, plus a worked example (`example_diff` + `example_finding`, supplied
together) are optional but sharply improve a small model's output.

```yaml
extra_lenses:
  - id: simplify
    title: Simplify or delete
    instructions: |
      Flag code that should not exist at all. The best code is the code you never
      wrote: prefer the standard library, an existing dependency, or one line over
      a new abstraction. Call out needless wrappers, premature generality, and
      "just in case" code with no caller.
    example_diff: |
      --- a/util.py
      +++ b/util.py
      @@ -4,1 +4,3 @@
       def get_name(user):
      +    name = user.name
      +    return name
    example_finding:
      path: util.py
      line: 5
      severity: low
      title: Needless local variable
      body: The temporary adds nothing; return user.name directly.
      suggestion: "    return user.name"
```

Lens definitions are **trusted config**: they go into the system prompt, so only
define them in files you control (committed `.lgtmaybe.yml` or repo skill files),
never from PR-author content. See
[Add a custom review lens](add-a-custom-lens.md) for a full walk-through.

Default: none.

### lens_paths

Load `extra_lenses` from separate **skill files** instead of inlining them — handy
for sharing a lens across repos or wiring lgtmaybe into an agent harness. Each
entry is a YAML file (one lens, or a list of lenses) or a directory of `*.yml` /
`*.yaml` lens files. Paths are resolved relative to where lgtmaybe runs (your repo
root). Lenses loaded this way are appended to any inline `extra_lenses`.

```yaml
lens_paths:
  - .lgtmaybe/skills            # a directory of one-lens-per-file skill files
  - team-lenses/house-style.yml # or a single file
```

Default: none.

## CLI flag overrides

Every config field can be overridden at the command line:

```bash
lgtmaybe review \
  --provider anthropic \
  --model claude-sonnet-4-6 \
  --min-severity high
```

Flags take precedence over `.lgtmaybe.yml`.
