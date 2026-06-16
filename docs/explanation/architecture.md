# Architecture

lgtmaybe is built on **hexagonal architecture** (ports and adapters). The core
never imports from the adapters; adapters implement abstract ports defined in
`core/ports.py`. This lets the parallel build tracks evolve independently and
lets tests swap in fakes without patching.

## Ports and adapters

```
          ┌─────────────────────────────────────────┐
          │               core                      │
          │                                         │
          │  ports.py: ProviderClient               │
          │             GitHubGateway               │
          │             ReviewEngine                │
          │                                         │
          │  models.py: ReviewConfig                │
          │              ReviewFinding              │
          │              ProviderResult             │
          │              PRContext                  │
          └───────────┬───────────────┬─────────────┘
                      │               │
          ┌───────────▼──┐    ┌───────▼──────────┐
          │  providers/  │    │    github/       │
          │  (litellm    │    │  (REST adapter)  │
          │   adapter)   │    └──────────────────┘
          └──────────────┘
```

**`core/ports.py`** — the seam. Three abstract base classes:

- `ProviderClient` — one method: `complete(messages, model)` returns a
  `ProviderResult` (text + token usage).
- `GitHubGateway` — `get_pr_context()` fetches the PR diff and metadata;
  `post_review()` posts batched inline comments and a summary.
- `ReviewEngine` — `review(ctx, cfg)` returns `(findings, summary)`.

The ports were frozen in the foundation step. Other tracks (providers, github,
engine, CLI) build against these stable signatures. Changing a port requires
consensus across all tracks.

## Review pipeline

The engine executes five composable stages in sequence:

```
fetch → compress → prompt → parse → post
```

1. **fetch** — `GitHubGateway.get_pr_context()` retrieves the PR diff and
   metadata from the GitHub REST API. No PR code is checked out or executed.
   The diff is treated as untrusted input throughout.

2. **compress** — the diff is filtered to remove generated files, lockfiles,
   minified assets, and vendored code. Path filters from `ReviewConfig` are
   applied. Each remaining hunk is then padded with surrounding context lines
   from the head revision of the file (fetched by the gateway, never a
   checkout), capped by `context_lines` and the remaining token budget. The
   result is batched to fit `max_input_tokens`. The expanded diff is for the
   model only — inline-comment positions are always rebuilt from the **real**
   diff at post time, so a finding on an added context line maps to nothing and
   is dropped rather than mis-posted.

3. **prompt** — a structured prompt is built requesting JSON output with the
   `ReviewFinding` schema (`severity`, `file`, `line`, `body`, `suggestion`).
   The prompt includes prompt-injection defense instructions to resist PR text
   that attempts to steer the reviewer.

4. **parse** — the model's response is parsed and validated against
   `ReviewFinding` using Pydantic. Findings below `min_severity` are dropped.
   Parse errors are logged and surfaced in the summary rather than silently
   discarded.

5. **post** — findings are batched into a single GitHub review request.
   The summary comment is updated idempotently using a hidden marker, so
   re-running lgtmaybe on the same PR does not create duplicate comments. Each
   inline comment is stamped with a hidden per-finding fingerprint; on a re-run,
   conversations whose finding is gone and whose thread GitHub marks outdated are
   replied to and resolved (`resolve_fixed`, default on). Resolving a review
   thread is the one operation the REST review API can't do, so this step uses
   GitHub's GraphQL API — best-effort, so a failure never blocks the review.

## Provider strategy and factory

Provider selection uses the **strategy pattern**: `--provider` picks a
`ProviderClient` strategy; a small factory constructs it. litellm normalises
all providers to one `completion()` call shape, so the factory is small and the
engine is provider-agnostic.

Credential resolution uses a **chain of responsibility**: each provider knows
how to locate its own credentials (ambient cloud creds, env var API key, or
none for ollama). lgtmaybe never stores or logs credentials.

## Reliability: retries, timeouts, and concurrency

The provider wrapper (`LiteLLMProvider`) and the engine cooperate so a flaky
network recovers but a dead-end failure surfaces fast:

- **Retries are classified, not blanket.** Transient failures — capacity rate
  limits (`429 rate_limit_exceeded`), timeouts, connection errors (e.g. an
  ollama server still warming up), 5xx — are retried with **exponential backoff
  and jitter** (up to four attempts). **Permanent** failures are *not* retried:
  bad credentials (`AuthenticationError`), malformed/unsupported requests
  (`BadRequestError`, including content-policy blocks), unknown models
  (`NotFoundError`), denied permissions, and **quota/billing** rate limits
  (`429 insufficient_quota` — "you exceeded your current quota"). Retrying a
  quota error can never succeed; stacked across every lens it only turns an
  instant "out of credit" into many minutes of wasted runner time, so lgtmaybe
  raises it immediately. An optional `fallback_model` is still tried once.

- **One retry layer.** litellm's own internal retry loop is disabled
  (`num_retries=0`) so failures aren't ground through two stacked backoff layers
  — lgtmaybe owns the retry policy in one place.

- **Per-request timeout.** Every model call carries a timeout: 60s for hosted
  providers, 300s for local ones (ollama, openai-compatible), overridable via
  `timeout` / `--timeout`. The posting workflows additionally set a job-level
  `timeout-minutes` so a wedged run can't hold a runner for GitHub's six-hour
  default.

- **Bounded fan-out.** The per-category lenses run concurrently for hosted
  providers, but the pool is **capped (4 workers)** so a single batch doesn't
  burst the whole lens set at the provider at once and trip a capacity 429 on a
  lower-tier account — the lenses run in a couple of waves instead, and per-call
  latency dominates so the wall-clock cost is small. ollama runs **serially**
  (one worker): a single local instance serves a model one request at a time, so
  concurrent calls would only queue up and time out.

## Dependency injection

The engine receives its ports by injection. In production the CLI wires real
adapters; in tests `tests/fakes/` provides drop-in fakes. No monkey-patching or
`unittest.mock` is needed at the engine level.

## Why not a plugin framework or event bus

Both were considered and explicitly skipped. The current set of providers fits
cleanly in a strategy + factory; a plugin registry would add indirection with no
present benefit. An event bus would complicate the linear pipeline without
enabling any feature the product needs. These can be revisited if a concrete
requirement arises.
