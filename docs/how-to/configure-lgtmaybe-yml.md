---
description: Configure lgtmaybe with a .lgtmaybe.yml file - provider, model, severity floor, lenses, caps, and other non-secret defaults.
---

# Configure .lgtmaybe.yml

Put a `.lgtmaybe.yml` file at the root of your repository to set how lgtmaybe
reviews it. lgtmaybe reads the file from the directory it runs in, which is the
repo root for both the CLI and the GitHub Action. Pass `--config PATH` (or the
Action's `config_path` input) to read a different file; an explicit path must
exist.

The file holds non-secret settings only. API keys stay in the environment or
your CI secrets.

## Contents

- [Full Example](#full-example)
- [Field Reference](#field-reference)
  - [provider](#provider)
  - [model](#model)
  - [fallback_model](#fallback_model)
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
  - [reflect](#reflect)
  - [min_confidence](#min_confidence)
  - [mid_review_retrieval](#mid_review_retrieval)
  - [incremental](#incremental)
  - [static_analysis](#static_analysis)
  - [triage_model](#triage_model)
  - [auto_diagram](#auto_diagram)
  - [auto_describe](#auto_describe)
  - [high_impact](#high_impact)
  - [risk](#risk)
  - [pr_labels](#pr_labels)
  - [finding_rules](#finding_rules)
  - [summary_template](#summary_template)
  - [resolve_fixed](#resolve_fixed)
  - [extra_lenses](#extra_lenses)
  - [lens_paths](#lens_paths)
  - [directory_rules](#directory_rules)
  - [project](#project)
- [Precedence and CLI Overrides](#precedence-and-cli-overrides)
- [Upgrading from 1.14](#upgrading-from-114)

## Full Example

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

## Field Reference

This page covers the fields most teams set. [Reference: Config](../reference/config.md)
lists every field with its type and default, including the tuning knobs not
described here (`max_tokens`, `reasoning_effort`, `temperature`, `language`,
`fail_on`, `max_review_seconds`, `max_review_tokens` and others).

### provider

Which LLM backend to use. One of `openai`, `openrouter`, `anthropic`, `zai`,
`bedrock`, `vertex`, `azure`, `ollama`, `openai-compatible`.

```yaml
provider: anthropic
```

If neither the file nor a flag sets `provider` and `model`, lgtmaybe falls back
to `ollama` with `llama3`.

### model

The model identifier for the chosen provider. The format varies by provider:

| Provider | Example model IDs |
|---|---|
| openai | `gpt-5.5` |
| anthropic | `claude-sonnet-4-6`, `claude-haiku-4-5` |
| openrouter | `anthropic/claude-sonnet-4.6` |
| zai | `glm-4.6`, `glm-4.7`, `glm-4.5-air` (GLM / Zhipu AI; newer `glm-5.x` IDs pass through too) |
| bedrock | `us.anthropic.claude-sonnet-4-6`, `us.anthropic.claude-haiku-4-5-20251001-v1:0` (prefer the cross-region inference profile; another provider's name like `gpt-6-sol` is invalid, Bedrock's is `openai.gpt-6-sol` - see [Review with Bedrock](review-with-bedrock-oidc.md)) |
| vertex | `gemini-3.8-flash`, `gemini-3.1-pro-preview` |
| azure | your deployment name, e.g. `my-gpt-4o-deployment`, not the upstream model ID - see [Review with Azure](review-with-azure.md) |
| ollama | `qwen3.6:27b`, `gemma4:e4b` |
| openai-compatible | the served model name, e.g. `deepseek-chat` or `meta-llama/Llama-3.1-8B-Instruct` (also set `api_base` - see [Use a custom OpenAI-compatible endpoint](use-a-custom-openai-compatible-endpoint.md)) |

Write the bare ID. lgtmaybe adds the provider's litellm prefix itself, so
`bedrock/us.anthropic.claude-sonnet-4-6` would end up double-prefixed.

### fallback_model

A second model for a review call the primary model cannot finish. It is off by
default and adds no calls to a healthy review.

Both models go through the same `provider`, `api_base` and credentials. An
OpenRouter review can fall back from a Google model to a Qwen model because
OpenRouter serves both; an `openai` review cannot fall back to an Anthropic
account. Cross-provider failover needs a separate review run.

```yaml
provider: openrouter
model: google/gemini-3.8-flash
fallback_model: qwen/qwen3.8-max
```

When the fallback runs depends on the failure:

| Primary model result | What lgtmaybe does |
|---|---|
| Transient provider failure, such as a connection error, capacity rate limit, or 5xx | Exhausts the primary model's bounded retries, then tries the fallback. |
| Permanent provider failure, such as an unknown model, rejected request, or exhausted quota | Skips further primary attempts and tries the fallback. A shared credential, quota or endpoint failure usually rejects both. |
| Output hits the `max_tokens` ceiling, or the reply stops mid-JSON | First retries on the primary with a smaller payload or lower `reasoning_effort`, as the token counts suggest. Tries the fallback only if that also fails. |

The fallback does not judge the primary's answer. A clean review, a weak
finding or zero findings never trigger it. Unparseable output has its own
bounded repair path.

The fallback gets a fresh request and retry budget, so its time and tokens add
to what the primary already spent, and its usage counts towards
`max_review_tokens` once the call returns. The review summary names every lens
the fallback answered. `--profile` prints a `models:` line, and
`--profile-json` records the model on each call (see
[Profile Reference](../reference/profile.md#the-models-line)).

If the fallback also fails, lgtmaybe marks the lens incomplete. It does not
walk a list of models or go back to the primary, and a review with no usable
results fails rather than posting a clean result. If a lens keeps needing the
second model, make that model the primary.

The `--fallback-model` flag and the Action's `fallback_model` input override
the file:

```bash
lgtmaybe review --fallback-model qwen/qwen3.8-max
```

See [Choose a Review Model](choose-a-review-model.md) for current model results.

### min_severity

The lowest severity to report. Findings below it are dropped. From low to high:
`info`, `low`, `medium`, `high`, `critical`.

```yaml
min_severity: medium   # drops info and low findings
```

Default: `low`, which drops only `info` findings. A `project.stage` of
`prototype` raises the default to `medium` (see [project](#project)).

### include_paths / exclude_paths

Glob patterns that restrict which changed files are reviewed. `include_paths`
is an allowlist and `exclude_paths` a denylist applied after it, so an exclude
always wins. Both default to empty, which reviews every file.

Patterns match the full repo-relative path, and a `**/` prefix also matches at
the repo root, so `**/*.lock` covers a root-level lockfile. The built-in skip
filter for generated, vendored and binary files runs first, so an
`include_paths` entry can't bring back a lockfile.

```yaml
include_paths:
  - "src/**"
exclude_paths:
  - "src/generated/**"
  - "**/*.lock"
```

### max_files

The maximum number of changed files to review. Files past the cap are skipped
and the summary says `Reviewed the top N of M changed files`. With
[`triage_model`](#triage_model) set, the cap drops the lowest-risk files;
otherwise files keep their diff order.

```yaml
max_files: 30
```

Default: `50`. A separate per-file cap, `max_file_diff_lines` (default `2000`,
`0` disables), skips any single file whose diff is longer than that and names it
in the summary. It catches generated data files that the name-based skip filter
can't recognise.

### max_input_tokens

The token budget for each model call. When the diff exceeds it, lgtmaybe splits
the review across batched calls. With `recursive` on (the default), a single
file larger than the budget is walked hunk by hunk instead of being cut short.

```yaml
max_input_tokens: 80000
```

Default: `100000`. When you leave it unset, lgtmaybe lowers it to fit a model
whose context window is smaller. A value you set is used as is.

### preset

How many model calls the review spends. `fast` (the default) covers the nine
everyday categories in four calls, one per concern:

| Call | Covers |
|---|---|
| security | security |
| correctness | correctness, plus stated intent when the PR states one |
| code health | performance, complexity, ponytail, deprecation |
| artefacts | tests, documentation |

The same four run on every provider. Worker count only changes whether they
overlap. A fifth `spec` call runs when the repository commits a specification
that matches the PR (see [categories](#categories)).

`full` runs one focused call per category, for release branches and deep
audits.

```yaml
preset: full
```

Default: `fast`. CLI: `--preset fast|full` (`--full` is shorthand); Action
input: `preset`. An explicit `categories` list overrides the grouping.

### categories

Which review lenses to run. Narrowing the list turns off the preset grouping:
each listed category runs as its own concurrent call, and the findings are
merged. Fewer categories means fewer calls and fewer tokens, at the cost of
coverage.

Choose from `security`, `correctness`, `deprecation`, `tests`, `documentation`,
`performance`, `complexity`, `intent`, `ponytail` and `spec`.

```yaml
categories:
  - security
  - correctness
```

Default: all ten. Two of them only run when there is something to check:

- `intent` checks the diff against the PR's stated intent: title, description
  and commit names on GitHub, or your `git log` commit names on the CLI. With
  no stated intent (for example, no commits beyond the base branch yet) it is
  skipped and costs nothing. Under `fast` it shares the correctness call.
- `spec` checks the diff against a committed specification (OpenSpec, GitHub
  Spec Kit or Kiro). With no matching spec it is skipped. Turn it off with
  `spec_review: false`, `--no-spec`, or the Action input `spec_review`.

The PR title, description and commit names reach the provider only through the
`intent` lens and the change overview (`auto_diagram`). To keep that text from
being sent at all, drop `intent` from `categories` and set `auto_diagram: false`.

`ponytail` is the "lazy senior dev" check: *the best code is the code you never
wrote*. It flags code that needn't exist (YAGNI, reinventing the standard
library, code that could be far shorter). See
[What gets reviewed](../explanation/what-gets-reviewed.md#ponytail-the-laziest-senior-dev-in-the-room).

### context_lines

The most unchanged lines added around each changed hunk, read from the head
revision of the file, so the model sees a change in the context of its
surrounding code. The full budget goes before the hunk, where the enclosing
signature and setup are, and a quarter of it (at least one line) goes after.
lgtmaybe uses the smaller of this value and what the token budget allows, so
the padding shrinks on large PRs. Set it to `0` to review the bare diff.

```yaml
context_lines: 10   # up to 10 lines before each hunk and 2 after; 0 disables
```

Default: `20`. CLI: `--context-lines`.

### function_context

Extends each hunk's leading padding up to the enclosing function or class
signature when that sits above the `context_lines` window. lgtmaybe finds the
boundary with ast-grep (parsing only, never running code) for Python,
JavaScript, TypeScript/TSX, Go, Rust, Java and Ruby, with a bounded reach so a
distant definition can't swamp the diff. Other languages, and any ast-grep
failure, fall back to the fixed-line padding.

```yaml
function_context: false   # fixed-line padding only
```

Default: `true`.

### timeout

The per-request timeout, in seconds, for each model call. Left unset, lgtmaybe
picks a default by provider:

| Provider | Default |
|---|---|
| ollama, openai-compatible | 1800 s, multiplied by `max_concurrency` (calls queue on a single-slot local server) and capped at `max_review_seconds`, but never below 1800 s. With the defaults that is 3600 s. |
| openrouter | 1800 s (it can route to slow reasoning models) |
| other cloud providers | 600 s |

An explicit value is used as is and never scaled. Raise it for a large local
model:

```yaml
timeout: 3600   # an hour per call, e.g. for a big model on CPU
```

CLI: `--timeout`; Action input: `timeout`. See
[Run locally with ollama](run-locally-with-ollama.md#slow-models-and-timeouts).

### structured_output

Asks the provider to return the findings JSON schema through its native JSON
mode (litellm `response_format`). This stops models, especially local ones, from
replying with prose instead of findings. Leave it on unless you hit one of two
failures:

1. The gateway **rejects** `response_format` outright (some
   `openai-compatible` gateways return a `400`). The review fails loudly; turn
   this off.
2. The gateway **accepts** JSON-schema mode but returns replies the parser reads
   as empty. This fails silently: `--profile` shows
   `findings: 0 parsed / 0 returned`, the run exits clean, and a capable model
   looks like one that finds nothing. This was seen with
   `anthropic/claude-fable-5.1` behind OpenRouter on lgtmaybe 2.3.0 and 2.7.0:
   the same diff returned 11 findings with this off. If a model is unexpectedly
   silent, re-run one diff with `--no-structured-output` before concluding it
   finds nothing.

Either way, the lenient parser still strips code fences and pulls JSON out of
surrounding prose.

```yaml
structured_output: false   # gateway rejects JSON-schema mode, or a model returns nothing under it
```

Default: `true`. CLI: `--no-structured-output`; Action input:
`structured_output`. See
[Use a custom OpenAI-compatible endpoint](use-a-custom-openai-compatible-endpoint.md#gateways-that-dont-support-json-mode-response_format).

### reflect

Runs the self-reflection pass, which audits the merged findings and drops the
ones the model marks low-confidence before anything is posted. It trims false
positives, so leave it on for most models. Turn it off for a weaker or local
model that drops valid findings during the audit.

```yaml
reflect: false   # keep every finding; skip the false-positive audit
```

Default: `true`. CLI: `--no-reflect`.

To audit a weak reviewer with a stronger model instead of turning the pass off,
set `reflect_model` (same provider and credentials as `model`). Under the
`fast` preset, unless you set `reflect_model`, an `anthropic` Sonnet or Opus
review audits with `claude-haiku-4-5`, and an `openai` GPT-5 review (other than
a mini or nano model) audits with `gpt-5-mini`. Every other combination audits
with the review model.

### min_confidence

During reflection, the auditor scores each kept finding from 0 (certainly a
false positive) to 10 (certainly real) by trying to disprove it against the diff
and the file text. Findings scored below `min_confidence` are dropped.

The score appears in the CLI output, the JSON output and, as a percentage, in
the posted comment's title next to the lens that raised it:
`**[HIGH · security · 80%] Title**`. A kept finding with no score always passes
the threshold, and its comment leaves out the percentage (as every comment does
with `reflect: false`).

```yaml
min_confidence: 5   # drop findings the auditor scores 0-4
```

Default: `0`, no numeric filtering; reflection prunes only through its keep/drop
verdicts. CLI: `--min-confidence`.

### mid_review_retrieval

Lets a review lens ask once for files it can't see.

By default every lens is told the diff is only a slice of the codebase. If a
finding depends on unseen code (a guard, a base class, a helper), the lens must
hedge, lower the severity, or say nothing. That keeps out confident cross-file
mistakes, but it also hides a real bug whose evidence is one file away.

With this on, a lens can name the files or symbols it needs alongside its
findings. lgtmaybe fetches them read-only (the same API or worktree read the
reflection auditor uses, never a checkout of PR code), redacts them, and re-runs
that one lens with them. Findings from both calls are kept and de-duplicated,
so a deferral can only add findings.

```yaml
mid_review_retrieval: true   # trade tokens for cross-file recall
```

Default: `false`, because it can cost up to one extra model call per batch and
lens, carrying the fetched text too. The other bounds are fixed: one hop (the
re-run can't ask again), at most five files within a quarter of
`max_input_tokens`, and a deferral that arrives past `max_review_seconds` or
`max_review_tokens` is skipped and reported in the summary. Read
[Reduce Review Cost](reduce-review-cost.md#what-costs-more-on-purpose) first,
and set `max_review_tokens` if the worst case matters. CLI:
`--mid-review-retrieval`; Action input: `mid_review_retrieval`.

### incremental

Commit-scoped review on GitHub. On a re-run, lgtmaybe reads a hidden watermark
from its own summary comment (the head SHA its last completed review covered)
and reviews only the commits pushed since, instead of the whole PR. New findings
post as inline comments. Findings on files outside the increment stay open, and
only a run that re-reviewed their file can auto-resolve them.

It falls back to a full review when there is no watermark yet, after a
force-push or rebase, or if the compare call fails. A failed review never moves
the watermark, so no commit is skipped. Comment `/review full` on the PR to
force a full re-review.

```yaml
incremental: false   # every run reviews the whole PR
```

Default: auto. Incremental on a `synchronize` push (new commits on a PR that was
already reviewed); full everywhere else, including open/reopen, slash commands
and the local CLI. Action input: `incremental`.

### static_analysis

Runs fast, deterministic tools over the changed files. Each tool feeds the
review in one of two modes:

- **`hint`**: the tool's findings become hints the model confirms, puts in
  context, or discards. This catches mechanical bugs models miss without posting
  raw linter noise; only findings the model confirms are reported. Default for
  ruff, bandit, mypy and semgrep.
- **`finding`**: the tool's findings are posted directly, with no model call.
  Free and identical run to run. Default for gitleaks, zizmor, ast-grep and
  osv-scanner.

The split follows the tool. A committed credential is either there or not, so
asking a model to "confirm or discard" a regex match only adds latency and a
chance of talking itself out of a real hit. A lint or SAST heuristic is often
technically true and beside the point, which is what a model is good at
filtering. Override either way with `tool_mode`.

| Tool | Checks |
|---|---|
| ruff, bandit, mypy | Python lint, security and type checks |
| gitleaks | secrets, any language |
| zizmor | GitHub Actions workflow security (template injection, unpinned `uses`, broad permissions); runs only when the PR changes a workflow |
| ast-grep | your own structural rules, set with `ast_grep_rules` (skipped when unset) - the deterministic sibling of `extra_lenses` |
| osv-scanner | known vulnerabilities in your dependencies |
| semgrep | multi-language SAST |

```yaml
static_analysis:
  enabled: true
  tools: [ruff, bandit, mypy, gitleaks, zizmor]  # default: all supported tools
  min_severity: low            # floor on mapped tool severity (default info)
  tool_min_severity:           # per-tool overrides of the floor
    ruff: medium               # only medium+ from ruff; bandit keeps low
  tool_mode:                   # per-tool overrides of hint vs finding
    gitleaks: hint             # route secrets through the model instead
  # semgrep_rules: .semgrep.yml  # replace the bundled MIT rule pack
  # ast_grep_rules: .ast-grep/   # your own structural rules
```

Default: `enabled: false`, so no tool ever runs. CLI:
`--static-analysis/--no-static-analysis`; Action input: `static_analysis`.

**Installing the tools.** A tool that isn't installed is skipped silently.
`pip install lgtmaybe[static-analysis]` installs ruff, bandit, mypy, semgrep and
zizmor, and ast-grep ships with lgtmaybe. gitleaks and osv-scanner are Go
binaries you install separately. The GitHub Action image includes all eight.

**Sandboxing.** The tools run on the already-fetched file texts in a throwaway
directory (never a checkout, never running PR code), in a subprocess with no
network, a scrubbed environment and a hard timeout. Their output is treated as
untrusted: redacted and wrapped before it reaches the model.

**Direct findings.** Tools read whole files, but only the diff is under review,
so a finding on a line the PR didn't change is dropped and counted in the
summary. Otherwise a fake credential in a test fixture would post on every PR
that touches the file. Direct findings are also capped per review, most severe
first. They carry a `scan:<tool>` category, so `finding_rules` can drop or
re-grade one scanner without turning it off:

```yaml
finding_rules:
  - match: {category: "scan:gitleaks", path: "tests/fixtures/**"}
    action: {drop: true}
```

**osv-scanner** answers a question a model can't: whether a pinned dependency
has a published advisory depends on what was disclosed this week, past any
model's knowledge cutoff. It reads the manifests and lockfiles the PR changes
through a scan-only channel that never enters the diff, a prompt or reflection.
Its findings are about a dependency rather than a line, so they render in the
review body, and only `high` and `critical` advisories show by default
(`unanchored_min_severity`).

It needs a local vulnerability database, since the sandbox has no network. The
Action image bakes one in at build time. On the CLI, point
`OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY` at a directory and seed it once from a
project that has your manifests:

```bash
export OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY="$HOME/.cache/osv-db"
osv-scanner scan source --offline-vulnerabilities --download-offline-databases .
```

Keep the variable set when you run lgtmaybe; it is the one variable passed into
the sandbox. Without a database the scanner reports nothing, and the review says
so rather than calling it clean.

**What the model stops being asked.** When osv-scanner posts findings, the
review prompt no longer asks the model whether a version has a known advisory
or a package is abandoned, since a knowledge cutoff can't answer either. The
model keeps deprecated APIs, end-of-life runtimes, typosquats and licence
conflicts. Likewise, when gitleaks posts findings the model is no longer asked
for hardcoded secrets: lgtmaybe redacts matched secrets before the diff leaves
for the provider, so the model couldn't see them anyway, while gitleaks reads
the unredacted text. Secrets written to a log are a different defect that no
secret scanner reports, so the model keeps that one. Set either tool to `hint`
and the model takes those checks back.

**semgrep** runs out of the box with a small, high-precision MIT rule pack that
ships with lgtmaybe; `semgrep_rules` replaces it. Rules are always read from a
local path, because semgrep's registry configs need the network. lgtmaybe
doesn't bundle the well-known semgrep-rules or opengrep-rules collections:
they are LGPL-2.1 plus a Commons Clause, which an MIT package can't
redistribute. Point `semgrep_rules` at a fuller pack you have obtained yourself.

**mypy** catches unguarded `Optional` bugs, such as a `dict.get()` result
dereferenced without a `None` check, which a review lens reads straight past. It
runs with `--ignore-missing-imports --follow-imports=skip` because the sandbox
holds only the changed files, so it reports what it can prove from one file and
stays quiet about the rest.

### triage_model

Two-stage routing, so routine PRs don't pay frontier prices while risky ones
still get the strong model. When set, this cheap model runs first over the
per-file diffs. It skips files that plainly need no review (pure formatting,
trivial renames, generated churn) and scores the rest 0-10 by risk. The strong
`model` then reviews only the survivors, riskiest first. Skipped files are
listed in the review summary, and `/review full` reviews everything.

Some files always go to the strong model, whatever triage says:
security-relevant paths (auth, crypto, session code, migrations, IaC, CI
workflows, dependency manifests), patches containing security-relevant tokens,
files with static-analysis hits, and large hunks. Any triage failure, such as an
unparseable verdict or a provider error, reviews everything.

`triage_model`, `model` and `reflect_model` share one provider and set of
credentials, so pointing them all at one ollama model costs nothing. The
trade-off is cheaper, faster reviews against the risk of triage under-rating a
subtle change. The always-review rules bound that risk; for maximum recall,
leave triage off.

```yaml
triage_model: claude-haiku-4-5   # cheap gatekeeper; unset = no triage
```

Default: unset (every file gets the full review). CLI: `--triage-model`; Action
input: `triage_model`.

### auto_diagram

Posts the change overview as a comment and refreshes it on every push: a
description of the change, a Risk of Change section, High Impact Areas, a
Mermaid flowchart of the components the PR touches and, when the change alters
a run-time flow, a Mermaid sequence diagram. It is one comment, updated in
place. `/diagram` posts the same overview on demand.

This is the switch for the whole comment. With it off, only the review itself
is posted automatically. It also stands down when the PR description or another
comment already has a diagram; the review summary says so, and `/diagram` still
works.

```yaml
auto_diagram: false
```

Default: `true`. Action input: `auto_diagram`. See
[Generate a change overview](generate-a-change-diagram.md).

### auto_describe

Heads the change overview with a structured description: a suggested title, the
change type, a short summary, a per-file walkthrough table and, when the PR
states an intent, a "does it do what it says" check. It is its own model call
and best-effort: a failure leaves a short note in its place and never blocks the
rest of the comment.

Because it rides the overview comment, it refreshes on every push. `/describe`
posts a description as a separate comment whether or not this is on.

```yaml
auto_describe: false
```

Default: `true`. Action input: `auto_describe`.

### high_impact

Includes the High Impact Areas section in the change overview: changes a
reviewer must not miss, across ten areas - infrastructure, security posture,
availability (anything that could cause an outage), data migrations, backups
and recovery, compatibility, observability, dependencies, cost and compliance.

Path patterns ground the model call as untrusted hints and also set a floor on
its output, so a sensitive file (a `*.tf`, a workflow, a migration, a backup
policy, a lockfile) is named even when the model says nothing about it. A failed
call falls back to that floor instead of dropping the section, and an empty
result says what was checked.

```yaml
high_impact: false
```

Default: `true`. Action input: `high_impact`. See
[Generate a change overview](generate-a-change-diagram.md).

### risk

Rates each PR's risk of change as `low`, `medium`, `high` or `critical`, with
the reasons. It is deterministic and makes no model call. It combines how many
workspace files import each changed file (Python and TS/JS, via ast-grep), the
High Impact path signals, the size of the change, and code changed without any
test changing.

The level and its top reason appear on the review summary line, the change
overview explains every reason, and a hidden `<!-- lgtmaybe-risk:<level> -->`
marker lets a workflow read it. With `pr_labels` on, the PR also gets a
`risk/<level>` label.

`core_paths` names modules the importer count can't see as central, such as a
config loader or a plugin registry. A changed file matching one is at least
`high`.

```yaml
risk:
  core_paths:
    - src/shared/**
    - "**/settings.py"
```

Default: on, with no `core_paths`. Turn it off with `risk: {enabled: false}`,
`--no-risk`, or the Action input `risk: false`. See
[Risk of Change](../explanation/risk-of-change.md) for how the level is decided
and how to gate on it.

### pr_labels

Adds labels derived from the finished review, with no extra model calls:

- `review-effort/1` to `review-effort/5`: a size estimate from the changed lines.
- `possible-security-issue`: a high or critical finding was posted by the
  security lens or by a security scanner (gitleaks, bandit, semgrep).
- `consider-splitting`: the diff spans many unrelated top-level directories.
- `risk/<level>`: the [risk of change](../explanation/risk-of-change.md)
  verdict, when `risk` is on. Only one level is applied at a time.

Labels are reconciled on each run (a stale `review-effort/2` is removed when the
score changes), and only lgtmaybe's own label families are touched. A labelling
failure never fails the review.

```yaml
pr_labels: true
```

Default: `false`. Action input: `pr_labels`.

### finding_rules

Declarative rules applied to findings just before posting. Rules can only drop
or re-grade findings; no user code runs. Each rule has a `match` (every field
given must match) and an `action`, and rules apply in order.

Match fields:

- `path`: a glob; a `**/` prefix also matches at the repo root.
- `category`: the lens that produced the finding (`security`, `correctness`, a
  custom lens ID, or `scan:<tool>`).
- `title_contains`: a case-insensitive substring.
- `min_severity`: matches findings at or above this severity.

Actions: `drop: true` or `set_severity: <level>`.

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

A custom template for the review summary line. Placeholders: `{count}`
(findings posted), `{provider}`, `{model}`, `{version}` (the lgtmaybe release
that produced the review) and `{risk}` (the risk segment, empty when `risk` is
off). A template that fails to format falls back to the built-in line.

```yaml
summary_template: "🤖 {count} finding(s) · {model} · lgtmaybe {version}"
```

Keep `{version}` if you can. The same model on the same provider reviews
differently across releases, so the version is what makes a surprising review
traceable.

Default: unset, which uses the built-in
`N findings · risk … · provider X · model Y · lgtmaybe Z` line.

### resolve_fixed

Resolves a review conversation once its finding is fixed. On a re-run, when a
finding lgtmaybe raised is no longer produced and GitHub marks its thread
outdated (the code under it changed), lgtmaybe replies `✅ Looks resolved.` and
resolves the conversation. Both conditions must hold, so a thread is never
closed just because nearby lines moved. Set it to `false` to resolve
conversations by hand.

GitHub resolves threads through its GraphQL API; the default `GITHUB_TOKEN` with
`pull-requests: write`, which posting already needs, is enough. The public
`lgtmaybe[bot]` App cannot resolve threads, so under `github_identity: lgtmaybe`
they stay open (see
[What the public App cannot do](use-as-github-action.md#what-the-public-app-cannot-do)).
GitLab has no
"outdated" signal, so there lgtmaybe resolves only threads its re-run validated
as fixed. Gitea's API can't resolve threads, and the local CLI has no
conversations, so both ignore this.

```yaml
resolve_fixed: false   # leave fixed conversations open for manual resolution
```

Default: `true`. Action input: `resolve_fixed`.

### extra_lenses

Your own review lenses, run alongside the built-in categories. Each one runs as
its own focused model call, and its findings merge into the same review. A lens
needs an `id` (unique, not a built-in category name, and not starting with
`scan:`) and `instructions`. A `title` and a worked example (`example_diff` and
`example_finding`, supplied together) are optional, but the example noticeably
improves a small model's output.

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

Lens text reaches the model as trusted instructions, so define lenses only in
files you control (the committed `.lgtmaybe.yml` or repo skill files), never
from PR-author content. See [Add a custom review lens](add-a-custom-lens.md)
for a walkthrough.

Default: none.

### lens_paths

Loads `extra_lenses` from separate skill files instead of inlining them, which
helps when sharing a lens across repos or wiring lgtmaybe into an agent harness.
Each entry is a YAML file (one lens or a list of lenses), a directory of `*.yml`
/ `*.yaml` lens files, or `pack:<name>` for a lens pack bundled with lgtmaybe.
Paths resolve relative to where lgtmaybe runs (your repo root). Loaded lenses
are appended to any inline `extra_lenses`.

```yaml
lens_paths:
  - .lgtmaybe/skills            # a directory of one-lens-per-file skill files
  - team-lenses/house-style.yml # or a single file
  - pack:robustness             # a bundled pack
```

Default: none. See
[Bundled lens packs](add-a-custom-lens.md#bundled-lens-packs-packname).

### directory_rules

Scopes extra review instructions and reference files to part of the repo, for
when `payments/**` and `tests/**` deserve different treatment. Each rule has
`paths` (globs; a `**/` prefix also matches at the repo root, and an empty or
missing list applies the rule everywhere), free-text `instructions`, and
`context_files` read from the checked-out workspace. A rule reaches every lens
call whose batch contains a file it matches.

```yaml
directory_rules:
  - paths: ["payments/**", "billing/**"]
    instructions: |
      Money-handling code. Treat rounding, currency-conversion and retry
      changes as high severity, and flag any write path that is not idempotent.

  - paths: ["tests/**"]
    instructions: |
      Test code. Do not flag duplication, long functions, or missing
      docstrings here.

  - paths: ["src/**"]
    instructions: Check changes against the architecture described below.
    context_files: [ARCHITECTURE.md]
```

Context files are redacted like the diff, share an eighth of
`max_input_tokens`, and are capped at five files per review. A missing or
over-budget file is skipped silently. Instructions and context are trusted
config: on `pull_request_target` they come from the base branch, never the PR
head. See
[Scope review instructions to a directory](scope-review-instructions-to-a-directory.md).

Default: no rules.

### project

Tells the reviewer what kind of project this is, so a weekend prototype and a
regulated payments service don't get the same review. See
[Calibrate reviews to your project](calibrate-reviews-to-your-project.md) for
the walkthrough.

```yaml
project:
  stage: regulated        # prototype | production | regulated
  domain: payments        # a short label, up to 80 characters
  notes: |                # up to 1,000 characters
    PCI-scoped card flows live under payments/. Most traffic is batch.
```

`stage` changes some defaults. A value you set yourself always wins.

| Stage | Default it changes | Review guidance |
|---|---|---|
| `prototype` | `min_severity` defaults to `medium` instead of `low` | Report correctness and security; skip hardening, observability, scalability and documentation polish |
| `production` | none | The standard bar |
| `regulated` | Any PR that isn't only docs and tests is at least `medium` [risk](#risk), with a `regulated workload` reason | Rate data handling, auditability, access control and personal-data exposure one severity step higher where the domain makes them material |

All three fields also add one short paragraph to the system prompt every lens
shares, so with prompt caching it is paid for once per run rather than per lens.
Leave the block out and the prompt is unchanged. Like `directory_rules`, this is
trusted config, read from the base branch on `pull_request_target`.

lgtmaybe doesn't read `AGENTS.md`, `CLAUDE.md` or `SOUL.md` for this. Those
files are written for coding agents, most of their text is noise to a reviewer,
and the larger ones don't fit the context-file budget. To put one in front of
the reviewer anyway, name it in a `directory_rules` entry's `context_files`.

Default: no profile.

## Precedence and CLI Overrides

Settings are merged from four layers, highest first:

1. CLI flags, or the GitHub Action's inputs.
2. `.lgtmaybe.yml` (or the file named by `--config` / `config_path`).
3. The user config at `~/.config/lgtmaybe/config.yml`, managed with
   `lgtmaybe config` (local `review` and `diagram` commands only).
4. Built-in defaults.

```bash
lgtmaybe review \
  --provider anthropic \
  --model claude-sonnet-4-6 \
  --min-severity high
```

Most single-value settings have a flag; run `lgtmaybe review --help` for the
list. List and nested settings are YAML-only: `include_paths`, `exclude_paths`,
`categories`, `finding_rules`, `extra_lenses`, `lens_paths`, `directory_rules`,
`project`, `spec_paths`, `risk.core_paths` and the `static_analysis` sub-keys.
The `--static-analysis` and `--risk` flags switch those features on or off and
keep the rest of their YAML settings.

## Upgrading from 1.14

Remove `prompt_cache` from `.lgtmaybe.yml` and `--prompt-cache` /
`--no-prompt-cache` from scripts. lgtmaybe 2.0 and later reject them. Prompt
caching is now automatic wherever the provider supports it.
