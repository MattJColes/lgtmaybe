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
  - [categories](#categories)
  - [context_lines](#context_lines)
  - [timeout](#timeout)
  - [structured_output](#structured_output)
  - [prompt_cache](#prompt_cache)
  - [reflect](#reflect)
  - [min_confidence](#min_confidence)
  - [incremental](#incremental)
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

### categories

Which review lenses to run. The reviewer asks for each category in its own
concurrent model call and merges the findings, so a focused prompt concentrates
on one concern at a time. One or more of `security`, `correctness`,
`deprecation`, `tests`, `documentation`, `performance`, `complexity`, `intent`,
`ponytail`. Narrowing the list trades thoroughness for fewer model calls (and
lower token usage).

The `ponytail` lens is the "lazy senior dev" check — *the best code is the code
you never wrote* — flagging code that needn't exist at all (YAGNI, reach for the
standard library, do it in fewer lines). See
[What gets reviewed](../explanation/what-gets-reviewed.md#ponytail-the-laziest-senior-dev-in-the-room).

The `intent` lens checks the diff against the PR's stated intent — title,
description, and commit names on GitHub; your `git log` commit names on the
CLI (in both branch and `--working` mode). When nothing states an intent (e.g.
no commits beyond the base branch yet), it is skipped automatically, so it
never costs an extra call. It is also the only lens that sends the PR
title/description/commit names to the provider — drop it from `categories` if
you don't want that text sent at all.

```yaml
categories:
  - security
  - correctness
```

Default: all nine categories.

### context_lines

Ceiling on the number of unchanged lines added around each changed hunk, read
from the head revision of the file so the model can review a change in the
context of its surrounding code. The pad is **asymmetric**: the full budget goes
before the hunk (the enclosing signature and setup explain a change best) and a
quarter of it — at least one line — goes after. The actual number used is the
smaller of this ceiling and what the token budget allows, so it shrinks
automatically on large PRs. Set it to `0` to disable context expansion and
review the bare diff (no extra file content is fetched).

```yaml
context_lines: 10   # at most 10 lines before each hunk (2 after); 0 disables
```

Default: `20`.

### timeout

Per-request timeout in seconds for each model call. Left unset, lgtmaybe picks a
**provider-aware default**: **300 s for ollama** (local models are slow) and 60 s
for cloud providers. Set it explicitly to raise it for a large local model.

```yaml
timeout: 900   # 15 minutes per call, e.g. for a big model on CPU
```

Default: auto (ollama 300 s, cloud 60 s). See
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

Cache the static system prompt across the per-lens review calls and the
reflection call. lgtmaybe fans out one model call per lens, and every one of
those calls shares the same large, static system prompt — on providers with an
explicit cache breakpoint (**anthropic**, and **bedrock** Claude/Nova models)
lgtmaybe marks that prompt with `cache_control` so every call after the first
reads it from the provider's prompt cache at the cached-input discount instead
of re-paying full input price. The diff and other per-PR content always stay
outside the cached region.

Support is feature-detected per model, and on every other provider (ollama,
`openai-compatible`, and providers that cache automatically server-side like
OpenAI) the request is sent unchanged — so leaving it on costs nothing. Turn it
off only to rule caching out while debugging provider behaviour. CLI:
`--no-prompt-cache`.

```yaml
prompt_cache: false   # send every call uncached, even on anthropic/bedrock
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
