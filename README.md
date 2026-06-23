<p align="center">
  <img src="docs/assets/logo.svg" alt="lgtmaybe logo — a shrugging face with curly-brace arms" width="128">
</p>

# lgtmaybe

Provider-agnostic PR reviewer. Seven hosted providers, local ollama, and any
OpenAI-compatible endpoint — one flag, no static keys for cloud providers. Posts
inline review comments and a summary.

📖 **Full documentation:** <https://mattjcoles.github.io/lgtmaybe/>

## What it reviews

lgtmaybe fetches the PR diff from the GitHub API and reviews the lines a pull
request changes. It never checks out or runs your code. To judge each change in
context it also reads a few surrounding lines from the file, but it only ever
comments on what the PR actually changed, not the whole repository.

Reviews surface the kind of thing a careful reviewer would flag, each graded from
`info` up to `critical`: **logic and correctness bugs** (edge cases, null
dereferences, off-by-one and boundary errors, mismatched ranges, unhandled error
paths, races and TOCTOU, missed `await`s, numeric and timezone bugs), **missing
or weak tests** for changed code paths (with a suggested test to drop in), and
**undocumented public APIs or stale docs** the change just made wrong. The model
is prompted with an **OWASP-aligned security checklist** — injection, XSS, CSRF
and open redirects, hardcoded secrets, broken authn/authz (including JWT
pitfalls), path traversal, unrestricted uploads, SSRF, insecure deserialization
and XXE, mass assignment, weak crypto, resource/DoS safety (including ReDoS),
secrets or PII (passwords, tokens, SSNs, card data) leaking into logs, and CI/IaC
misconfiguration (workflow script injection, unpinned actions, broad IAM, public
buckets) — so security findings are first-class, not an afterthought. It also
flags **factually outdated** code — deprecated APIs, end-of-life or vulnerable
dependencies, typosquat-looking additions — when the diff shows them,
**performance regressions** (N+1 queries, accidentally quadratic work, redundant
computation, allocations or blocking I/O on hot paths, unbounded queries, caches
that never evict), and needless **complexity** (deep nesting / high cyclomatic
complexity, over-long functions, duplicated logic). An **intent lens** checks
that the PR does what it says: it reads the PR title, description, and commit
names (or your `git log` commit names on the CLI) and flags out-of-scope hunks,
code that contradicts the stated intent, and promised behaviour the diff never
implements. A **ponytail lens** — the "lazy senior dev" (the best code is the
code you never wrote) — flags code that needn't exist at all: YAGNI, reach for
the standard library, do it in fewer lines. Generated and non-reviewable files (lockfiles, minified bundles,
vendored directories, binaries) are skipped automatically, and secrets are
redacted from the diff before it is sent to the model.

**Hardened against malicious PRs.** lgtmaybe never checks out or runs PR code,
treats the diff as untrusted input, defends against prompt injection (including
forged delimiter break-out attempts), and redacts a broad set of secret formats
(cloud keys, GitHub/Slack/Google/Stripe tokens, private keys, passwords, and
credentials in connection strings) before anything leaves your environment. See
[Data and Privacy](docs/explanation/data-and-privacy.md).

**How the scope is bounded.** Every run is capped so a large PR can't blow up
latency:

- `max_files` (default 50) — reviews the top-N changed files and notes how many were skipped.
- `max_input_tokens` (default 100k) — batches the diff to fit the model's budget.
- `recursive` (default on) — when a single file's diff exceeds that budget, walks it hunk-by-hunk instead of sending it whole; `--no-recursive` sends files whole.
- `categories` (default all nine) — which review lenses to run; each is a concurrent model call, so narrowing the list means fewer calls.
- `min_severity` (default `low`) plus `include_paths` / `exclude_paths` — focus the review on what you care about.

See [Configure .lgtmaybe.yml](docs/how-to/configure-lgtmaybe-yml.md) for every knob.

**Big files, small models.** When one file's diff is too big for a single model
call, lgtmaybe reviews it **hunk-by-hunk** rather than whole, so the model never
loses the tail of a large file. On by default; `--no-recursive` turns it off.
Smaller local models gain the most — across 8 runs on two fixtures, a local
**qwen3.5:4b** averaged **88% recall** reviewing hunk-by-hunk versus **61%**
reviewing files whole, with its *worst* run matching the whole-file method's
*best* (a real effect, not noise), at ~2.4× the tokens. See
[the benchmark](DEVELOPMENT.md#benchmarking-the-recursive-rlm-walk).

**What you get back.** Each finding is structured data — file, line, severity, a
title, an explanation, and an optional suggested fix — so it renders the same
everywhere:

- **On a GitHub PR** — an inline comment on the exact changed line for each finding, plus one summary comment naming the model used. Re-running updates the same comments instead of duplicating them, auto-resolves a conversation once its finding is fixed, and a clean PR gets a 👍 **LGTM!**.
- **On the CLI** — `lgtmaybe review` reads your local `git` diff and prints the findings (a readable listing, a JSON array with `--json`, or `--format agent` for an AI coding agent to read and apply); nothing is posted to GitHub.

<p align="center">
  <img src="docs/assets/review-sql-injection.png" alt="An inline lgtmaybe review comment on a GitHub pull request flagging a [CRITICAL] SQL injection vulnerability, with an explanation and a suggested parameterized-query fix" width="640">
</p>

<p align="center"><em>On a GitHub PR — an inline comment on the changed line. The same findings on the CLI:</em></p>

<p align="center">
  <img src="docs/assets/cli-example.png" alt="The lgtmaybe review command running in a terminal, printing a finding with its file, line, severity, and a summary line naming the model" width="640">
</p>

A fuller walkthrough with example output is in
[What gets reviewed](docs/explanation/what-gets-reviewed.md).

## Quick start (60 seconds, local, zero cost)

From inside a git repo, on a branch with changes, review your diff against the
remote primary branch and print the findings:

```bash
pip install lgtmaybe

lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

No GitHub token and no pull request needed — `lgtmaybe review` reads your local
`git` diff and prints the findings. To post reviews on real pull requests, wire
up the [GitHub Action](#use-as-a-github-action). See
[Getting Started](docs/tutorial/getting-started.md) for the full walkthrough.

> **Picking a model:** use a **coding** model, and **bigger/newer is more
> accurate**. Our benchmark numbers are for a small `qwen3.5:4b`; a larger,
> current coding model catches more. See
> [Which model?](docs/how-to/run-locally-with-ollama.md#which-model-and-will-it-fit).

## Providers

| Provider | Auth |
|---|---|
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `zai` | `ZAI_API_KEY` — GLM / Zhipu AI (`glm-4.6`, `glm-4.7`, `glm-4.5-air`, …; newer `glm-5.x` too). Optional `--api-base` for the China / coding-plan endpoint |
| `bedrock` | Ambient AWS creds — GitHub OIDC, no static key |
| `vertex` | Ambient GCP creds — Workload Identity Federation, no key |
| `azure` | Ambient Azure AD creds — GitHub OIDC, no static key (or `AZURE_API_KEY`) + endpoint |
| `ollama` | None — local only, zero cost |
| `openai-compatible` | Any OpenAI `/v1` endpoint via `--api-base` (DeepSeek, llama.cpp, LM Studio, vLLM). Key optional — `--api-key` / `OPENAI_COMPATIBLE_API_KEY`, or none for local servers |

## Documentation

Browse the rendered docs at <https://mattjcoles.github.io/lgtmaybe/>, or read the
Markdown sources below.

**Tutorial** — learn by doing

- [Getting Started](docs/tutorial/getting-started.md) — your first review with ollama

**How-to guides** — task recipes

- [Run locally with ollama](docs/how-to/run-locally-with-ollama.md)
- [Use a custom OpenAI-compatible endpoint](docs/how-to/use-a-custom-openai-compatible-endpoint.md)
- [Review with Bedrock OIDC](docs/how-to/review-with-bedrock-oidc.md)
- [Review with Vertex WIF](docs/how-to/review-with-vertex-wif.md)
- [Review with Azure OpenAI](docs/how-to/review-with-azure.md)
- [Use as a GitHub Action](docs/how-to/use-as-github-action.md)
- [Configure .lgtmaybe.yml](docs/how-to/configure-lgtmaybe-yml.md)
- [Releasing (maintainers)](docs/how-to/releasing.md)

**Reference** — look things up

- [Configuration Reference](docs/reference/config.md) — all config fields and schemas (generated)

**Explanation** — understand the design

- [What gets reviewed](docs/explanation/what-gets-reviewed.md) — scope, caps, and what the output looks like
- [Architecture](docs/explanation/architecture.md) — ports and adapters, the review pipeline
- [Auth Model](docs/explanation/auth-model.md) — why keyless cloud, how credential resolution works
- [Data and Privacy](docs/explanation/data-and-privacy.md) — what is sent where, secret redaction, ollama local mode
- [Trust and Cost](docs/explanation/trust-and-cost.md) — choosing who reviews run for (everyone, trusted contributors, or admins) and the small cost angle

## Use as a GitHub Action

```yaml
name: lgtmaybe

on:
  pull_request_target:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    if: ${{ github.event_name == 'pull_request_target' || github.event.issue.pull_request }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: MattJColes/lgtmaybe@v0
        with:
          provider: openai
          model: gpt-5.5
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

Copy-paste workflows for every cloud and API-key provider live in
[`examples/workflows/`](examples/workflows/). Cloud providers (Bedrock, Vertex,
Azure) are **keyless** — pass `aws_role_arn` / `gcp_wif_provider` /
`azure_client_id` and the action does the OIDC/WIF exchange for you (needs
`id-token: write`). See
[Use as a GitHub Action](docs/how-to/use-as-github-action.md). ollama is local
only — run it through the [CLI](docs/how-to/run-locally-with-ollama.md) instead.

> **🔧 Choose who can trigger reviews.** You decide who reviews run for —
> everyone, trusted contributors, or just admins. The example workflows default
> to trusted contributors (`OWNER`, `MEMBER`, `COLLABORATOR`), and it's a
> one-line change to open it up or tighten it. With ollama this is free; on a
> hosted provider it also keeps token spend predictable. See
> [Who can trigger a review](docs/how-to/use-as-github-action.md#who-can-trigger-a-review)
> and [Trust and Cost](docs/explanation/trust-and-cost.md).

## Distribution

- **CLI** — `pip install lgtmaybe`
- **GitHub Action** — `uses: MattJColes/lgtmaybe@v0`

## Contributing

Test-first, green CI, scope is the gate. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see `LICENSE`.
