# Architecture

A map of **lgtmaybe** — a provider-agnostic PR reviewer that posts inline review
comments plus a summary. One core, two distribution variants (a PyPI CLI and a
GitHub Action), and a single `--provider` flag that selects the LLM backend.

This document is the high-level orientation: the project layout, the LLM
providers, the components inside the application, and the user-facing features.
For the *why* behind the design (ports & adapters, the pipeline, the patterns),
see [`docs/explanation/architecture.md`](docs/explanation/architecture.md); for
the decisions that are settled, see [`CLAUDE.md`](CLAUDE.md). Per-capability
behavior lives in the living specs under [`openspec/specs/`](openspec/specs/),
each requirement bound to the code it describes by ast-grep anchors (see
"Living specs" in `CLAUDE.md`).

## Design in one breath

lgtmaybe is **hexagonal** (ports & adapters). The core defines three ports in
`core/ports.py`; the outside world (litellm, the code hosts, git) plugs in as
adapters. The engine depends only on the ports, so **which model reviews** and
**which host is posted to** are independent choices that swap without it
noticing — and tests inject fakes instead of patching.

```
        CLI flags / Action inputs / .lgtmaybe.yml
                          │
                          ▼
                   ReviewConfig                 (core/models.py)
                          │
   ┌──────────────────────┴───────────────────────┐
   │                    engine                      │   depends only on ports
   │   redact → split → cap → expand → batch        │
   │        → fan-out per lens (preset: fast=4      │
   │          calls / full=9; one global pool,      │
   │          cached preamble+diff prefix) → parse  │
   │        → merge/dedupe → reflect → filter       │
   └───────┬───────────────────────────┬───────────┘
           │ ProviderClient            │ ReviewGateway
           ▼                           ▼
   providers/ (litellm)        github/ · gitlab/ · gitea/
           │                    (REST)  ·  local/ (git)
           │                           │
           ▼                           ▼
   OpenAI · Anthropic ·         inline comments + summary
   OpenRouter · z.ai ·          on a pull / merge request
   Bedrock · Vertex · Azure ·   (or CLI stdout)
   Ollama · OpenAI-compatible
```

Neither column knows about the other, so any of the nine model backends can
review a change on any of the three hosts.

## Project layout

```
lgtmaybe/
├── src/lgtmaybe/            # the application (≈3.2k LOC)
│   ├── __main__.py          # `python -m lgtmaybe` / Docker ENTRYPOINT → Click CLI
│   │
│   ├── core/                # the hexagon's centre — no outward dependencies
│   │   ├── ports.py         #   ProviderClient · ReviewGateway · Supports* · ReviewEngine
│   │   ├── models.py        #   pydantic data contracts (ReviewConfig, ReviewFinding, …)
│   │   ├── forge.py         #   which code host: Forge · PRLocator · URL parsing · tokens
│   │   ├── diffparse.py     #   unified-diff primitives (file split, hunk headers)
│   │   ├── diff.py          #   commentable-line index · is_reviewable() skip filter
│   │   ├── findings.py      #   stable hidden finding ids (fingerprint · identity)
│   │   ├── comment.py       #   the Markdown every host posts (badges, demoted sections)
│   │   └── logging.py       #   structured JSON logs with secret redaction
│   │
│   ├── engine/              # the review pipeline (adapter-agnostic)
│   │   ├── engine.py        #   LLMReviewEngine — orchestrates every stage
│   │   ├── redact.py        #   scrub secrets from the diff before egress
│   │   ├── injection.py     #   prompt-injection defense + delimiter break-out guard
│   │   ├── compress.py      #   token-aware batching + hunk context expansion
│   │   ├── prompt.py        #   per-category system prompts (OWASP checklist, etc.)
│   │   ├── parse.py         #   lenient JSON → ReviewFinding parsing; names the failure shape
│   │   ├── repair.py        #   one reformat re-ask at a reply that would not parse
│   │   └── reflect.py       #   self-reflection pass that drops low-confidence findings
│   │
│   ├── providers/           # LLM adapter (the ProviderClient side)
│   │   ├── litellm_provider.py  # litellm wrapper: retries (tenacity) + fallback
│   │   ├── factory.py       #   (Provider, model) → configured client; timeouts; model strings
│   │   ├── credentials.py   #   chain-of-responsibility credential resolver
│   │   └── constants.py     #   shared provider defaults (e.g. ollama base URL)
│   │
│   ├── github/              # GitHub adapter (a ReviewGateway) — the complete one
│   │   ├── rest_gateway.py  #   fetch PR context · post review · in-thread replies
│   │   └── checkout.py      #   read-only base-branch clone for symbol resolution
│   ├── gitlab/              # GitLab adapter — discussions, REST thread resolution
│   ├── gitea/               # Gitea adapter — immutable reviews, pre-post de-dupe
│   │
│   ├── lenses/              # bundled opt-in lens packs (design/robustness/interface/frontend), loaded via pack:<name>
│   ├── local/               # local-mode adapter: build a PRContext from `git`
│   ├── config/              # layered config: defaults < user file < repo file < flags
│   │   ├── loader.py        #   merge layers → ReviewConfig
│   │   └── store.py         #   ~/.config/lgtmaybe user config (never stores keys)
│   │
│   └── cli/                 # Click entrypoints + wiring
│       ├── __init__.py      #   execute_* logic; wires real adapters into the engine
│       ├── commands.py      #   command + option declarations (review/diagram/comment/action/config)
│       ├── slash.py         #   /review /improve /ask /describe /diagram slash commands
│       ├── runtime.py       #   per-invocation options bag (creds, PR URL)
│       └── render.py        #   local output: human / json / agent formats
│
├── tests/                   # mirrors src/ ; fakes in tests/fakes/, snapshots, fixtures
├── evals/                   # offline scoring harness against fixture diffs
├── docs/                    # MkDocs site: tutorial / how-to / reference / explanation
├── examples/workflows/      # one ready-to-copy GitHub workflow per model provider
├── examples/gitlab/         # ready-to-copy .gitlab-ci.yml
├── examples/gitea/          # ready-to-copy Gitea Actions workflow
│
├── action.yml              # composite Action: keyless OIDC/WIF auth → docker run GHCR image
├── Dockerfile              # lean runtime image (uv sync --no-dev --frozen)
├── pyproject.toml          # package metadata, deps, ruff/mypy/pytest config (the CI gate)
├── CLAUDE.md               # settled decisions for contributors/agents
└── ARCHITECTURE.md         # this file
```

## LLM providers

One `--provider` flag, one [litellm] `completion()` call shape underneath. Nine
backends, each with its own native auth (resolved by the credential chain in
`providers/credentials.py`):

| Provider     | litellm prefix | Auth model                                                        |
|--------------|----------------|-------------------------------------------------------------------|
| `openai`     | `openai/`      | API key (`--api-key` / `OPENAI_API_KEY`)                          |
| `anthropic`  | `anthropic/`   | API key (`--api-key` / `ANTHROPIC_API_KEY`)                       |
| `openrouter` | `openrouter/`  | API key (`--api-key` / `OPENROUTER_API_KEY`)                      |
| `zai`        | `zai/`         | API key (`--api-key` / `ZAI_API_KEY`); optional `api_base` for the China / coding-plan endpoint |
| `bedrock`    | `bedrock/`     | **ambient AWS creds** (GitHub OIDC role or local `~/.aws`) — no static key |
| `vertex`     | `vertex_ai/`   | **ambient GCP creds** (Workload Identity Federation or local ADC) — no static key |
| `azure`      | `azure/`       | API key **or** keyless Azure AD/Entra token (OIDC); needs the resource endpoint |
| `ollama`     | `ollama/`      | none — just an `api_base`; fully local, zero cost                 |
| `openai-compatible` | `openai/` (custom base) | requires an `api_base`; key optional (placeholder for keyless local servers — llama.cpp / LM Studio / vLLM) |

**The wedge:** first-class **Bedrock + Vertex + Azure with keyless OIDC/WIF**, so cloud
reviews need no static keys in GitHub secrets. The `LiteLLMProvider` adds retries
(exponential backoff + jitter, 4 attempts) and an optional `--fallback-model`.

## Code hosts

A second axis, independent of the model provider: which forge the review is
posted to. `core/forge.py` parses a change-request URL into a `PRLocator`
(forge + host + repo + number) and names the token variable; `cli` maps the
resolved forge to its adapter.

| Forge | URL shape | Token | Entrypoint |
|---|---|---|---|
| GitHub | `/pull/42` | `GITHUB_TOKEN` | `lgtmaybe action` (GitHub Actions) |
| GitLab | `/-/merge_requests/42` | `GITLAB_TOKEN` | `lgtmaybe gitlab-ci` (CI_* vars) |
| Gitea | `/pulls/42` | `GITEA_TOKEN` | `lgtmaybe action` (same runtime as GitHub) |

`ReviewGateway` requires only three methods — fetch context, post a review, post
a comment. Everything richer (incremental review, thread resolution, labels,
checks, feedback, file reads) is an optional `Supports*` protocol the CLI probes
for and skips when absent. That is what lets an adapter be **honest** about what
its host cannot do rather than failing at run time: Gitea claims neither
incremental review nor thread resolution, and GitLab claims thread resolution
but not (yet) incremental.

The engine never sees a gateway at all — it takes a `PRContext` and returns
findings — which is why `local/` can produce one from `git` with no host
involved.

## Components inside the application

**Core (`core/`)** — the dependency-free centre.
- **Ports** (`ports.py`): `ProviderClient`, `GitHubGateway`, `ReviewEngine` —
  the three abstract seams, frozen so the rest builds against stable signatures.
- **Models** (`models.py`): frozen pydantic contracts with `extra="forbid"` —
  `ReviewConfig`, `ReviewFinding`/`ReviewResult`, `ProviderResult`, `PRContext`,
  the `Severity`/`ReviewCategory`/`Provider` enums, and the reflection envelope.
- **Diff primitives** (`diffparse.py`) and **secret-safe structured logging**
  (`logging.py`).

**Engine (`engine/`)** — the pipeline, as composable stages:
`redact → split per file → drop non-reviewable → file-cap → expand hunks with
budget-scaled context → batch to token budget → fan out one call per review
lens → parse → merge & dedupe → self-reflect → filter by severity →
findings + summary`. The lens set follows the `preset` — `fast` (default)
covers seven code-focused categories in four calls when the pool can overlap
work, or three combined calls with one worker; `full` runs all nine — and every
(batch, lens) call shares one pool (`max_concurrency`: 8 cloud, 1
ollama/openai-compatible) with a cacheable preamble-plus-diff prompt prefix on
anthropic/bedrock. A soft whole-review deadline (`max_review_seconds`) degrades
an overrunning review to partial-with-a-notice, and every stage and model call
is timed (`--profile` prints the breakdown). It fails loud
(`ReviewIncompleteError`) rather than report a false "clean" when every model
call fails.

**Provider adapter (`providers/`)** — strategy + factory over litellm, with the
credential chain of responsibility and a provider-aware timeout (ollama gets a
long one, cloud short).

**GitHub adapter (`github/`)** — `RestGitHubGateway` reads PR context (diff,
files, head-revision file text — all read-only API, never a checkout) and posts a
single batched review, updated idempotently via a hidden per-provider marker
comment. `diff.py` builds the commentable-line index (the `line`+`side` anchors a
review comment can attach to) and the `is_reviewable` skip filter (lockfiles,
minified, vendored, generated, binary).

**Local adapter (`local/`)** — builds a `PRContext` by shelling out to `git`, so
`lgtmaybe review` works on a branch or working tree with no GitHub at all.

**Config (`config/`)** — layered precedence (built-in defaults → user file →
repo `.lgtmaybe.yml` → CLI flags / Action inputs); the user store deliberately
refuses to persist API keys.

**CLI (`cli/`)** — the Click surface: `review` (local), `diagram` (local change
diagrams — structure and sequence), `comment` (issue_comment slash commands), `action` (the
container entrypoint that routes by event), and a `config` group. Slash commands
(`/review`, `/improve`, `/ask`, `/describe`, `/diagram`) route to the same
engine/provider.

## Features

**Review intelligence** — nine review lenses, fanned out per the `preset`:
`fast` (default) covers seven in four concurrent calls when parallelism is
available — security, correctness flow/intent, correctness state/lifecycle, and
merged code health — while a single-worker provider keeps correctness combined
for three calls. `full` restores tests and documentation and runs each lens as
its own focused call with a lens-matched worked example. Findings from every
call are merged & de-duped:
- **Security** — OWASP-aligned checklist: injection, XSS, CSRF/open redirect,
  hardcoded secrets, broken authn/authz (incl. JWT pitfalls), path traversal,
  unrestricted upload, SSRF, insecure deserialization/XXE, mass assignment, weak
  crypto, sensitive-data/PII exposure, CI/IaC misconfiguration, resource/DoS
  (incl. ReDoS).
- **Correctness & logic** — null derefs, off-by-one/boundary, inverted ranges,
  unhandled error paths, bad conditionals, resource leaks, races/TOCTOU and
  async mistakes, numeric and date/time bugs, aliasing/mutation.
- **Deprecation & dependency health** — deprecated APIs, EOL runtimes, abandoned
  or vulnerable dependencies, typosquat/license red flags (when the diff shows
  them).
- **Test coverage** — missing tests for changed paths, with a runnable test in
  the suggestion; weak tests (assertion-free, over-mocked, sleep-based) too.
- **Intent** — does the PR do what it says? Checks the diff against the PR
  title/description/commit names (CLI: `git log` commit names), flagging
  out-of-scope hunks and unfulfilled claims. Skipped when nothing states an
  intent; the intent text is redacted + wrapped as untrusted data and only this
  lens's call carries it.
- **Documentation** — undocumented or mis-described public surfaces only.
- **Performance** — N+1 queries, accidentally quadratic work, redundant
  computation, hot-path allocations/blocking I/O, unbounded queries (graded by
  impact).
- **Complexity** — high cyclomatic complexity / deep nesting, over-long
  functions, duplicated logic, dead code (restrained, `info`/`medium`).
- **Ponytail** — the "lazy senior dev" lens (the best code is the code you never
  wrote): needless code (YAGNI), reinventing the standard library, code that could
  be far shorter, premature configurability (restrained, `info`/`medium`).

**Custom lenses (BYO)** — beyond the nine built-ins, users define their own
lenses in trusted config (`extra_lenses` inline, or skill files via the loader's
`lens_paths`). Each `CustomLens` (`id` + `instructions`, optional `title` and a
worked `example_diff`/`example_finding`) is fanned out as its own focused call
through the same merge/dedupe/reflect pipeline. Lens text goes into the system
prompt, so it must come from config you control, never PR-author content.

**Output & posting** — structured findings (path, line, severity, title, body,
optional suggestion). On GitHub: inline comments on the exact changed line + one
summary naming the model, updated idempotently (no duplicates), with a 👍 **LGTM!**
on a clean PR. On the CLI: `human`, `json`, or `agent` (instructions an AI coding
agent can apply) formats.

**Reviewer hardening** (so a malicious PR can't subvert the reviewer):
- **Fork safety** — runs on `pull_request_target` for secrets but never checks
  out or executes PR code; the diff is fetched via API and treated as untrusted.
- **Prompt-injection defense** — the diff is wrapped as untrusted data and forged
  `DIFF_START`/`DIFF_END` delimiters are neutralised so it can't break out.
- **Secret redaction** before egress — AWS/OpenAI/GitHub/Slack/Google/Stripe
  keys, PEM private keys, and quoted password / `Authorization` / connection-
  string credentials are scrubbed before the diff reaches the LLM.
- **Schema enforcement** — `extra="forbid"` rejects drifted/injected fields.

**Scope & cost control** — `max_files`, `max_input_tokens`, `context_lines`,
`min_severity`, `include_paths`/`exclude_paths`, and `categories` bound every
run; generated/binary files are skipped automatically.

**Reliability** — provider retries with fallback (all attempts for one call
share a 2.5×-timeout budget), provider-aware timeouts, a soft whole-review
deadline (`max_review_seconds`) that degrades an overrunning run to partial
results with an explicit notice (a SIGINT/SIGTERM from a cancelled or
timed-out CI job winds the run down the same way), a self-reflection pass to cut
false positives (toggle with `--no-reflect`; its verdicts carry a 0–10
confidence score, filtered by `min_confidence`), and loud failure surfacing (a
"review failed" comment + non-zero exit) rather than silent passes. Every
stage and model call is timed; `--profile` prints the breakdown.

**Grounding** — optional static-analysis fusion (`static_analysis`, default
off): installed deterministic linters (ruff, bandit, mypy, semgrep with local rules)
run over the fetched changed-file texts in a sandboxed, network-less
subprocess, and their findings enter each lens prompt as untrusted hints to
confirm or discard — never posted verbatim.

**Cost** — on providers with an explicit prompt-cache breakpoint (anthropic,
bedrock Claude/Nova) every review call shares a cacheable prefix: the
lens-independent system preamble plus the wrapped diff, with the lens-specific
instruction as the final uncached user block — so the fan-out re-reads the
whole preamble-plus-diff at the cached-input discount instead of re-processing
the diff once per lens, and a per-batch warm-up primer writes the prefix once
before the rest of the batch dispatches (`prompt_cache`, default on;
feature-detected, plain merged requests elsewhere). On a
`synchronize` push the review is commit-scoped **incremental** by default:
only the diff since the last completed review (a hidden watermark in the
summary comment) is re-reviewed, falling back to a full review on a
force-push or first run (`incremental`; `/review full` on demand). Optional
two-stage **triage** (`triage_model`, default off): a cheap model skips
plainly-non-substantive files and ranks the rest by risk before the strong
model reviews the survivors — bounded by a deterministic security floor that
always escalates auth/crypto/IaC/CI paths, security tokens, static-analysis
hits, and large hunks.

**Distribution** — `pip install lgtmaybe` (PyPI CLI) and the composite GitHub
Action (keyless OIDC/WIF cloud auth, then runs the GHCR image). Release is
trusted-publishing (OIDC, no tokens) on a `v*.*.*` tag.

[litellm]: https://github.com/BerriAI/litellm
