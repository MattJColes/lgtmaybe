<p align="center">
  <img src="docs/assets/logo.svg" alt="lgtmaybe logo: a shrugging face with curly-brace arms" width="128">
</p>

# lgtmaybe

AI code review for **GitHub, GitLab, and Gitea**, or your local Git diff. Choose
from seven hosted providers, ollama, or any OpenAI-compatible endpoint. On a
pull or merge request, lgtmaybe posts inline findings and a summary; the CLI
prints findings locally. Bedrock, Vertex, and Azure support keyless cloud auth.

📖 **Full documentation:** <https://lgtmaybe.coles.codes/>

## What It Reviews

lgtmaybe fetches a pull or merge request's diff through the code host API, or
reads your local Git diff. It reads surrounding lines for context and comments
only on changed lines. It never checks out or runs pull request code.

Findings are graded from `info` to `critical`. The built-in lenses look for:

- **Correctness and security:** logic errors, missed `await`s, injection,
  broken authorisation, leaked secrets, and CI configuration risks.
- **Tests and documentation:** missing coverage for changed behaviour,
  undocumented public APIs, and docs made stale by the change.
- **Code health:** deprecated APIs, risky dependencies, performance
  regressions, and needless complexity.
- **Intent:** changes that contradict the PR title, description, or commits,
  or leave promised behaviour unimplemented.
- **Ponytail:** code that need not exist, including places where the standard
  library or a simpler approach would do.

[What gets reviewed](docs/explanation/what-gets-reviewed.md) has the full scope
and examples.

Generated files, lockfiles, vendored code, and binaries are skipped. The diff
is treated as untrusted input, and detected secrets are redacted before any
model call. See [Data and Privacy](docs/explanation/data-and-privacy.md) for
what is sent to a provider and how prompt injection is handled.

**Fast by default.** The `fast` preset covers all nine built-in categories in
four model calls: security, correctness, code health, and artefacts (tests and
documentation). `--preset full` runs each category separately for a deeper audit. A committed
spec that matches the change adds one spec-review call. All calls share one
concurrency limit, and providers that support prompt caching reuse the diff
prefix. Add `--profile` to see time and token use.

**Large changes stay bounded.** The main controls are:

- `max_files` (default 50) limits how many changed files are reviewed and
  reports the rest as skipped.
- `max_input_tokens` (default 100k, fitted down to the model's context window
  when left unset) splits the diff into batches.
- `recursive` (on by default) reviews an oversized file hunk by hunk;
  `--no-recursive` turns this off.
- `max_concurrency` (default 6) limits simultaneous model calls. Local server
  settings also affect how many calls can run at once.
- `categories`, `min_severity`, and path filters narrow what runs and what is
  reported. An explicit category list runs one call per selected lens.

See [Configure .lgtmaybe.yml](docs/how-to/configure-lgtmaybe-yml.md) for every
setting. For the measured recall and token cost of hunk-by-hunk review on a
small local model, see
[the recursive-review benchmark](DEVELOPMENT.md#benchmarking-the-recursive-rlm-walk).

**What you get back.** Each finding has a file, line, severity, title,
explanation, and sometimes a suggested fix:

- **On a pull or merge request**, findings appear on the changed lines, with
  one summary naming the model. A re-run on the same commit updates that
  summary, a new commit gets a new one, and findings already posted are not
  repeated. A clean review gets a 👍 **LGTM!**. GitHub and GitLab resolve a
  finding's conversation once a later run shows it fixed; Gitea cannot. See
  [Where It Posts](#where-it-posts).
- **On the CLI**, `lgtmaybe review` prints findings without posting them.
  Choose readable output (the default), JSON (`--json`), instructions for a
  coding agent (`--format agent`), or a SARIF log for GitHub code scanning
  (`--format sarif`).

Slash commands on a pull or merge request: `/review` and `/improve` refresh the
review, `/ask <question>` answers in the conversation, `/describe` posts a
structured description, and `/diagram` posts a change overview. The overview
covers the risk of change, high-impact areas, and a Mermaid flowchart, plus a
sequence diagram when the change alters a runtime flow. Run `lgtmaybe diagram`
for a local text version. See
[Generate a change overview](docs/how-to/generate-a-change-diagram.md).

On GitHub, a push triggers an incremental review of only the new commits.
Optional `triage_model` lets a cheap model skip plainly non-substantive files.
Optional `static_analysis` runs installed tools: ruff, bandit, mypy, and semgrep
feed hints to the model, while gitleaks, zizmor, ast-grep, and osv-scanner post
deterministic findings directly. See
[Reduce review cost](docs/how-to/reduce-review-cost.md).

<p align="center">
  <img src="docs/assets/marketplace/marketplace-screenshot-1.png" alt="An inline lgtmaybe review comment on a GitHub pull request flagging a [CRITICAL] SQL injection vulnerability, with an explanation and a suggested parameterised-query fix" width="640">
</p>

<p align="center"><em>On a GitHub PR: an inline comment on the changed line. The same findings on the CLI:</em></p>

<p align="center">
  <img src="docs/assets/cli-example.png" alt="The lgtmaybe review command running in a terminal, printing a finding with its file, line, severity, and a summary line naming the model" width="640">
</p>

A fuller walkthrough with example output is in
[What gets reviewed](docs/explanation/what-gets-reviewed.md).

## Quick Start (Local, No API Key)

Start ollama and pull a model as shown in
[Getting Started](docs/tutorial/getting-started.md). Then, from a Git branch
with changes, review the diff against the primary branch:

```bash
pip install lgtmaybe        # or Homebrew/WinGet: see docs/how-to/install-the-cli.md

lgtmaybe config init        # detects ollama or your API key, checks it, saves it
lgtmaybe review
```

`config init` finds a running ollama (and its pulled models) or a provider API
key in your environment, suggests a current model, checks it with one tiny
request, and saves the provider and model (never the key). You can skip it and
pass `--provider ollama --model qwen3.6:27b` instead; with neither, `review`
stops at once and says so rather than guessing a model.

You need no GitHub token and no pull request: `lgtmaybe review` reads your
local `git` diff and prints the findings. `lgtmaybe diagram` takes the same
flags and prints the change overview: what the change is, its high-impact
areas, and a picture of the components it touches and the flow it alters. Run
`review` then `diagram` before opening a pull request. See
[Generate a change overview](docs/how-to/generate-a-change-diagram.md). To run
the review from your Git hooks on every push, use the
[pre-commit hooks](docs/how-to/review-before-you-commit.md).

`lgtmaybe --help` lists every command with usage examples,
`lgtmaybe review --help` shows the everyday options, and
`lgtmaybe review --help-all` adds the advanced tuning ones. To post reviews on real pull
requests, set up the [GitHub Action](#use-as-a-github-action), or on another
host, [GitLab CI](docs/how-to/review-on-gitlab.md) or
[Gitea Actions](docs/how-to/review-on-gitea.md).
[Getting Started](docs/tutorial/getting-started.md) has the full walkthrough,
and [Choose a review model](docs/how-to/choose-a-review-model.md) compares
models.

## Providers

| Provider | Auth | Guide |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | [OpenAI](docs/how-to/review-with-openai.md) |
| `anthropic` | `ANTHROPIC_API_KEY` | [Claude](docs/how-to/review-with-anthropic.md) |
| `openrouter` | `OPENROUTER_API_KEY` | [OpenRouter](docs/how-to/review-with-openrouter.md) |
| `zai` | `ZAI_API_KEY` for GLM / Zhipu AI models (`glm-4.6`, `glm-4.7`, `glm-4.5-air`, `glm-5.x`). Optional `--api-base` for the China / coding-plan endpoint | [z.ai (GLM)](docs/how-to/review-with-zai.md) |
| `bedrock` | Ambient AWS creds via GitHub OIDC, no static key | [Bedrock](docs/how-to/review-with-bedrock-oidc.md) |
| `vertex` | Ambient GCP creds via Workload Identity Federation, no key | [Vertex](docs/how-to/review-with-vertex-wif.md) |
| `azure` | Resource endpoint, plus `AZURE_API_KEY` or keyless Entra (Azure AD) creds via GitHub OIDC | [Azure](docs/how-to/review-with-azure.md) |
| `ollama` | None: local only, zero cost | [ollama](docs/how-to/run-locally-with-ollama.md) |
| `openai-compatible` | Any OpenAI `/v1` endpoint via `--api-base` (DeepSeek, llama.cpp, LM Studio, vLLM). Key optional: `--api-key` / `OPENAI_COMPATIBLE_API_KEY`, or none for local servers | [Local & OpenAI-compatible](docs/how-to/use-a-custom-openai-compatible-endpoint.md) |

## Where It Posts

The model provider and the code host are independent choices: any provider
above works on any host below.

| Host | How it runs | Token | Guide |
|---|---|---|---|
| GitHub | GitHub Action (`MattJColes/lgtmaybe@v2`) | `GITHUB_TOKEN` | [GitHub Action](docs/how-to/use-as-github-action.md) |
| GitLab | GitLab CI job (`lgtmaybe gitlab-ci`) | `GITLAB_TOKEN` | [Review on GitLab](docs/how-to/review-on-gitlab.md) |
| Gitea | Gitea Actions (same container) | `GITEA_TOKEN` | [Review on Gitea](docs/how-to/review-on-gitea.md) |
| None | `lgtmaybe review` on your local diff | n/a | [Install the CLI](docs/how-to/install-the-cli.md) |

The review is the same everywhere: same lenses, same reflection pass, same
findings. What differs is what each host's API can do with the result:

| | GitHub | GitLab | Gitea |
|---|---|---|---|
| Inline comments + summary | ✅ | ✅ | ✅ |
| Slash commands | ✅ | ✅ | ✅ |
| Auto-resolve a fixed finding | ✅ | ✅ | ✗ no thread API |
| Incremental re-review | ✅ | not yet | ✗ no compare diff |
| Keyless cloud auth (OIDC/WIF) | ✅ | ✗ use an API key | ✗ use an API key |

## Documentation

Browse the rendered docs at <https://lgtmaybe.coles.codes/>, or read the
Markdown sources below. For LLM agents, a curated
[`llms.txt`](https://lgtmaybe.coles.codes/llms.txt) index and a whole-corpus
[`llms-full.txt`](https://lgtmaybe.coles.codes/llms-full.txt) are published at
the docs root.

**Tutorial:** learn by doing

- [Getting Started](docs/tutorial/getting-started.md): your first review with ollama

**How-to guides:** task recipes

- [Install the CLI](docs/how-to/install-the-cli.md)
- [Choose a review model](docs/how-to/choose-a-review-model.md)
- [Use as a GitHub Action](docs/how-to/use-as-github-action.md)
- [Post as lgtmaybe[bot]](docs/how-to/post-as-a-github-app.md)
- [Review on GitLab](docs/how-to/review-on-gitlab.md)
- [Review on Gitea](docs/how-to/review-on-gitea.md)
- [Review with OpenAI](docs/how-to/review-with-openai.md)
- [Review with Claude (Anthropic)](docs/how-to/review-with-anthropic.md)
- [Review with OpenRouter](docs/how-to/review-with-openrouter.md)
- [Review with z.ai (GLM)](docs/how-to/review-with-zai.md)
- [Review with Bedrock OIDC](docs/how-to/review-with-bedrock-oidc.md)
- [Review with Vertex WIF](docs/how-to/review-with-vertex-wif.md)
- [Review with Azure OpenAI](docs/how-to/review-with-azure.md)
- [Run locally with ollama](docs/how-to/run-locally-with-ollama.md)
- [Local models & other OpenAI providers](docs/how-to/use-a-custom-openai-compatible-endpoint.md)
- [Configure .lgtmaybe.yml](docs/how-to/configure-lgtmaybe-yml.md)
- [Add a custom review lens](docs/how-to/add-a-custom-lens.md)
- [Scope instructions to a directory](docs/how-to/scope-review-instructions-to-a-directory.md)
- [Calibrate reviews to your project](docs/how-to/calibrate-reviews-to-your-project.md)
- [Generate a change overview](docs/how-to/generate-a-change-diagram.md)
- [Fix findings with an AI agent](docs/how-to/fix-findings-with-an-ai-agent.md)
- [Review before you commit (pre-commit hooks)](docs/how-to/review-before-you-commit.md)
- [Upload findings as SARIF](docs/how-to/upload-findings-as-sarif.md)
- [Reduce review cost](docs/how-to/reduce-review-cost.md)
- [Releasing (maintainers)](docs/how-to/releasing.md)

**Reference:** look things up

- [Configuration Reference](docs/reference/config.md): every config field and schema (generated)
- [Profile output](docs/reference/profile.md): what `--profile` prints

**Explanation:** understand the design

- [What gets reviewed](docs/explanation/what-gets-reviewed.md): scope, caps, and what the output looks like
- [Risk of change](docs/explanation/risk-of-change.md): how the low/medium/high/critical verdict is reached
- [Architecture](docs/explanation/architecture.md): ports and adapters, the review pipeline
- [Auth Model](docs/explanation/auth-model.md): why keyless cloud, how credential resolution works
- [Data and Privacy](docs/explanation/data-and-privacy.md): what is sent where, secret redaction, ollama local mode
- [Trust and Cost](docs/explanation/trust-and-cost.md): who reviews run for (everyone, trusted contributors, or admins) and what that costs

## Use as a GitHub Action

Use lgtmaybe from the
[GitHub Marketplace](https://github.com/marketplace/actions/lgtmaybe). It is a
GitHub Action, so its settings live in your workflow. In
`.github/workflows/lgtmaybe.yml`, set `provider`, `model`, and the matching
authentication input in the step's `with:` block. This complete example uses
OpenAI and runs reviews only for trusted contributors:

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
    # Trusted authors only, and a comment starts a job only when it carries
    # one of lgtmaybe's slash commands (issue_comment fires on every comment).
    if: >-
      (github.event_name == 'pull_request_target' &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
      (github.event.issue.pull_request &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association) &&
       (contains(github.event.comment.body, '/review') ||
        contains(github.event.comment.body, '/improve') ||
        contains(github.event.comment.body, '/ask') ||
        contains(github.event.comment.body, '/describe') ||
        contains(github.event.comment.body, '/diagram')))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7 # base repo only, for .lgtmaybe.yml
      - uses: MattJColes/lgtmaybe@v2
        with:
          provider: openai
          model: gpt-5.5
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

Using a different **model provider**? Copy-paste workflows for every cloud and
API-key provider live in [`examples/workflows/`](examples/workflows/). Cloud
providers (Bedrock, Vertex, Azure) are **keyless**: pass `aws_role_arn`,
`gcp_wif_provider`, or `azure_client_id` and the action does the OIDC/WIF
exchange for you (it needs `id-token: write`). See
[Use as a GitHub Action](docs/how-to/use-as-github-action.md). ollama is local
only, so run it through the [CLI](docs/how-to/run-locally-with-ollama.md)
instead.

### Not on GitHub?

lgtmaybe runs the same review on GitLab and Gitea. Only the wiring changes:

- **GitLab:** a CI job running `lgtmaybe gitlab-ci`, gated on merge request
  pipelines. See [Review on GitLab](docs/how-to/review-on-gitlab.md) and
  [`examples/gitlab/`](examples/gitlab/).
- **Gitea:** Gitea Actions runs the same container as GitHub, because it
  reimplements the same runtime. See
  [Review on Gitea](docs/how-to/review-on-gitea.md) and
  [`examples/gitea/`](examples/gitea/).

Keyless cloud auth is a GitHub Actions feature, so on GitLab and Gitea use an
API-key provider, or `ollama` against a runner-local model for zero cost.

By default, reviews post as `github-actions[bot]`. To post as
`lgtmaybe[bot]`, install the public
[lgtmaybe App](https://github.com/apps/lgtmaybe/installations/new), grant the
workflow `id-token: write`, and add `github_identity: lgtmaybe` beside the
provider settings. You never receive or manage the App's private key. lgtmaybe
still runs as the Action in your workflow; the App changes only the GitHub
author identity. See [Post as lgtmaybe[bot]](docs/how-to/post-as-a-github-app.md).

> **🔧 Choose who can trigger reviews.** Reviews can run for everyone, trusted
> contributors, or just admins. The example workflows default to trusted
> contributors (`OWNER`, `MEMBER`, `COLLABORATOR`), and it's a one-line change
> to open it up or tighten it. With ollama this is free; on a hosted provider it
> also keeps token spend predictable. See
> [Who can trigger a review](docs/how-to/use-as-github-action.md#who-can-trigger-a-review)
> and [Trust and Cost](docs/explanation/trust-and-cost.md).

## Distribution

- **CLI (PyPI):** `pip install lgtmaybe`
- **CLI (Homebrew):** `brew tap MattJColes/tap && brew trust MattJColes/tap && brew install lgtmaybe`
  ([details](docs/how-to/install-the-cli.md); the `brew trust` step is required for third-party taps)
- **CLI (WinGet, Windows x64):** `winget install --id MattJColes.lgtmaybe --exact`
  ([details](docs/how-to/install-the-cli.md#install-on-windows-winget))
- **GitHub Action:** `uses: MattJColes/lgtmaybe@v2`

## Contributing

Test-first, green CI, scope is the gate. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT. See `LICENSE`.
