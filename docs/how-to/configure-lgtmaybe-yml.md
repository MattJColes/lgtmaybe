# Configure .lgtmaybe.yml

Place a `.lgtmaybe.yml` file at the root of your repository to control how
lgtmaybe reviews pull requests. CLI flags override file values; the file
provides defaults for all runs.

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

Which LLM backend to use. One of `openai`, `openrouter`, `anthropic`,
`bedrock`, `vertex`, `ollama`.

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
| bedrock | `us.anthropic.claude-sonnet-4-6`, `us.anthropic.claude-haiku-4-5` (prefer the cross-region inference profile; a non-Bedrock id like `openai.gpt-5.5` is invalid — see [Review with Bedrock](review-with-bedrock-oidc.md)) |
| vertex | `gemini-3-pro`, `gemini-3.5-flash` |
| azure | your deployment name, e.g. `my-gpt-4o-deployment` (not the upstream model id — see [Review with Azure](review-with-azure.md)) |
| ollama | `qwen3.6:27b`, `gemma4:e4b` |

### min_severity

The minimum severity level to report. Findings below this threshold are
suppressed. Ordered low to high: `info`, `low`, `medium`, `high`, `critical`.

```yaml
min_severity: medium   # suppresses info and low findings
```

Default: `info` (all findings reported).

### include_paths / exclude_paths

Glob patterns to restrict which files in the diff are reviewed.
`include_paths` acts as an allowlist; `exclude_paths` acts as a denylist applied
after the allowlist. Both default to empty (all files included).

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

Hard cap on the number of tokens sent to the model. If the compressed diff
exceeds this limit, lgtmaybe truncates it and notes the truncation in the
summary.

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

Ceiling on the number of unchanged lines added above and below each changed hunk,
read from the head revision of the file so the model can review a change in the
context of its surrounding code. The actual number used is the smaller of this
ceiling and what the token budget allows, so it shrinks automatically on large
PRs. Set it to `0` to disable context expansion and review the bare diff (no
extra file content is fetched).

```yaml
context_lines: 10   # at most 10 lines either side of each hunk; 0 disables
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
[Run a local or OpenAI-compatible model](run-a-local-model.md#slow-models-and-timeouts).

### structured_output

Constrain the model to emit the findings JSON schema using the provider's native
JSON mode (litellm `response_format`). This keeps models — especially local ones —
from returning prose or reasoning instead of findings. Leave it on unless a
particular model/provider doesn't support structured output, in which case the
lenient parser is the fallback.

```yaml
structured_output: false   # only if your model rejects JSON-schema mode
```

Default: `true`.

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
  --model claude-haiku-4-5 \
  --min-severity high
```

Flags take precedence over `.lgtmaybe.yml`.
