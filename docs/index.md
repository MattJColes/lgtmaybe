---
description: Provider-agnostic AI code review for pull and merge requests on GitHub, GitLab, Gitea and Azure DevOps, using OpenAI, Claude, Bedrock, Vertex, Azure, ollama or any OpenAI-compatible endpoint. Inline review comments, keyless OIDC/WIF cloud auth, GitHub Action, GitLab CI and CLI.
---

<div class="hero" markdown>

![lgtmaybe logo: a shrugging face with curly-brace arms](assets/logo.svg){ width="128" }

# lgtmaybe

</div>

lgtmaybe reviews pull and merge requests on **GitHub, GitLab, Gitea, and Azure
DevOps**. It can also review a local Git diff. Choose a hosted
model, ollama, or another OpenAI-compatible endpoint. It posts inline comments
and one summary, or prints findings locally.

It reads the diff and a little surrounding code, but comments only on changed
lines. It never checks out or runs the change. Generated files and binaries are
skipped, secrets are redacted, and all author-supplied text is treated as
untrusted.

It checks for:

- **Correctness and security:** logic errors, missed `await`s, injection, auth mistakes, and leaked secrets.
- **Code health:** performance problems, needless complexity, deprecations, and risky dependencies.
- **Tests and documentation:** missing or weak tests, stale comments, and undocumented APIs.
- **Intent:** whether the change does what its title, description, and commits say it does.
- **Ponytail:** code you don't need, and simpler or standard-library ways to do the job.
- **Architecture:** coupling, wrong-way dependencies, and broken subclass contracts that will be hard to undo.

Findings are graded `info` to `critical` and land on the changed line
they refer to. A clean change gets a 👍 **LGTM!**.

![An inline lgtmaybe review comment flagging a [CRITICAL] SQL injection vulnerability, with an explanation and a committable parameterised-query suggestion](assets/marketplace/marketplace-screenshot-1.png){ width="720" }

Slash commands can refresh a review (`/review`, `/improve`), answer a question
(`/ask`), post a structured description (`/describe`), or post
[a change overview](how-to/generate-a-change-diagram.md) (`/diagram`). The
overview rates the risk of change, lists high-impact areas, and draws a
flowchart, plus a sequence diagram when the change alters a runtime flow. Run
`lgtmaybe diagram` to print a text version locally.

```mermaid
flowchart LR
    web["Storefront<br/>React<br/>places orders"]
    api["Order API<br/>Python<br/>creates orders (changed)"]
    db["Order database<br/>PostgreSQL<br/>stores orders"]
    queue["Order events<br/>SQS<br/>buffers events (new)"]
    worker["Notification worker<br/>Python<br/>sends confirmations (new)"]
    email["Email provider<br/>delivers messages"]
    web -->|calls| api
    api -->|stores in| db
    api -->|publishes to| queue
    queue -->|delivers to| worker
    worker -->|sends via| email
```

## Start here

<div class="grid cards" markdown>

- **Tutorial:** [Getting started](tutorial/getting-started.md): your first review with ollama, locally and free.
- **How-to:** [install the CLI](how-to/install-the-cli.md), [choose a review model](how-to/choose-a-review-model.md), [run locally](how-to/run-locally-with-ollama.md), [Bedrock OIDC](how-to/review-with-bedrock-oidc.md), [Vertex WIF](how-to/review-with-vertex-wif.md), [Azure OpenAI](how-to/review-with-azure.md), [GitHub Action](how-to/use-as-github-action.md), [GitLab](how-to/review-on-gitlab.md), [Gitea](how-to/review-on-gitea.md), [Azure DevOps](how-to/review-on-azure-devops.md).
- **Reference:** [Configuration](reference/config.md): every config field and schema.
- **Explanation:** [What gets reviewed](explanation/what-gets-reviewed.md), [Architecture](explanation/architecture.md), [Auth model](explanation/auth-model.md), [Data & privacy](explanation/data-and-privacy.md).

</div>

## What's New

The latest releases, built from the changelog every time the docs publish.

<!-- whats-new -->

## Providers

| Provider | Auth |
|---|---|
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `zai` | `ZAI_API_KEY` for GLM / Zhipu AI; optional `--api-base` for the China / coding-plan endpoint |
| `bedrock` | Ambient AWS creds via GitHub OIDC, no static key |
| `vertex` | Ambient GCP creds via Workload Identity Federation, no key |
| `azure` | Resource endpoint, plus `AZURE_API_KEY` or keyless Entra (Azure AD) creds via GitHub OIDC |
| `ollama` | None: local only, zero cost |
| `openai-compatible` | `--api-base` to any OpenAI `/v1` endpoint; key optional (placeholder for keyless local servers) |

## Where It Posts

The model provider and the code host are independent: any provider above works
on any host below.

| Host | How it runs | Token | Guide |
|---|---|---|---|
| GitHub | GitHub Action | `GITHUB_TOKEN` | [GitHub Action](how-to/use-as-github-action.md) |
| GitLab | GitLab CI job | `GITLAB_TOKEN` | [Review on GitLab](how-to/review-on-gitlab.md) |
| Gitea | Gitea Actions | `GITEA_TOKEN` | [Review on Gitea](how-to/review-on-gitea.md) |
| Azure DevOps | Azure Pipelines job | `SYSTEM_ACCESSTOKEN` or `AZURE_DEVOPS_TOKEN` | [Review on Azure DevOps](how-to/review-on-azure-devops.md) |
| None | `lgtmaybe review` locally | n/a | [Install the CLI](how-to/install-the-cli.md) |

The same review runs on every host. They differ in thread resolution,
incremental reviews, and keyless cloud auth. See
[Architecture](explanation/architecture.md#code-hosts-forges) for the details.

## For AI Agents

The site root publishes a curated [`llms.txt`](llms.txt) index of these docs,
plus a whole-corpus [`llms-full.txt`](llms-full.txt), for LLM crawlers and
coding agents.
