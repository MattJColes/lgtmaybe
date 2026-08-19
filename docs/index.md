---
description: Provider-agnostic AI code review for pull and merge requests on GitHub, GitLab and Gitea — OpenAI, Claude, Bedrock, Vertex, Azure, ollama and any OpenAI-compatible endpoint. Inline review comments, keyless OIDC/WIF cloud auth, GitHub Action + GitLab CI + CLI.
---

<div class="hero" markdown>

![lgtmaybe logo — a shrugging face with curly-brace arms](assets/logo.svg){ width="128" }

# lgtmaybe

</div>

lgtmaybe reviews the code a pull request changes. Pick OpenAI, Claude, Bedrock,
Vertex, Azure, ollama, or any OpenAI-compatible endpoint, then run it on
**GitHub, GitLab, or Gitea** — or from your terminal. The change gets inline
comments and one summary; locally, you get the same findings before you push.

It reads the diff and a little surrounding code, but only comments on changed
lines. It never checks out or runs the change. Generated files and binaries are
skipped, secrets are redacted, and all author-supplied text is treated as
untrusted.

It checks for:

- **Correctness and security** — logic errors, missed `await`s, injection, auth mistakes, and leaked secrets.
- **Code health** — performance problems, needless complexity, deprecations, and risky dependencies.
- **Tests and documentation** — missing or weak tests, stale comments, and undocumented APIs.
- **Intent** — whether the change does what its title, description, and commits say it does.
- **Ponytail** — code you don't need, standard-library opportunities, and simpler ways to get the job done.

Findings are graded from `info` to `critical` and land on the exact changed
line. A clean change just gets a 👍 **LGTM!**.

![An inline lgtmaybe review comment flagging a [CRITICAL] SQL injection vulnerability, with an explanation and a committable parameterized-query suggestion](assets/marketplace/marketplace-screenshot-1.png){ width="720" }

Reviews aren't all it does. **`/review`** and **`/improve`** run the review,
**`/describe`** writes a structured overview, **`/diagram`** draws
[the change](how-to/generate-a-change-diagram.md) — a flowchart of what it
touches, plus a sequence diagram of the flow it alters — and
**`/ask <question>`** answers in the change. Run `lgtmaybe diagram` to draw the
same map locally before you push.

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

- **Tutorial** — [Getting started](tutorial/getting-started.md): your first review with ollama, locally and free.
- **How-to** — task recipes: [choose a review model](how-to/choose-a-review-model.md), [run locally](how-to/run-locally-with-ollama.md), [Bedrock OIDC](how-to/review-with-bedrock-oidc.md), [Vertex WIF](how-to/review-with-vertex-wif.md), [Azure OpenAI](how-to/review-with-azure.md), [GitHub Action](how-to/use-as-github-action.md), [GitLab](how-to/review-on-gitlab.md), [Gitea](how-to/review-on-gitea.md).
- **Reference** — [Configuration](reference/config.md): every config field and schema.
- **Explanation** — [What gets reviewed](explanation/what-gets-reviewed.md), [Architecture](explanation/architecture.md), [Auth model](explanation/auth-model.md), [Data & privacy](explanation/data-and-privacy.md).

</div>

## Providers

| Provider | Auth |
|---|---|
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `zai` | `ZAI_API_KEY` — GLM / Zhipu AI; optional `--api-base` for the China / coding-plan endpoint |
| `bedrock` | Ambient AWS creds — GitHub OIDC, no static key |
| `vertex` | Ambient GCP creds — Workload Identity Federation, no key |
| `azure` | Ambient Azure AD creds — GitHub OIDC, no static key (or `AZURE_API_KEY`) + endpoint |
| `ollama` | None — local only, zero cost |
| `openai-compatible` | `--api-base` to any OpenAI `/v1` endpoint; key optional (placeholder for keyless local servers) |

## Where it posts

The model provider and the code host are independent — any provider above works
on any host below.

| Host | How it runs | Token | Guide |
|---|---|---|---|
| GitHub | GitHub Action | `GITHUB_TOKEN` | [GitHub Action](how-to/use-as-github-action.md) |
| GitLab | GitLab CI job | `GITLAB_TOKEN` | [Review on GitLab](how-to/review-on-gitlab.md) |
| Gitea | Gitea Actions | `GITEA_TOKEN` | [Review on Gitea](how-to/review-on-gitea.md) |
| None | `lgtmaybe review` locally | — | [Install the CLI](how-to/install-the-cli.md) |

The review is identical on all three. What differs is what each host's API can
do with the result — auto-resolving a fixed finding, reviewing only new commits,
and keyless cloud auth are not available everywhere. See
[Architecture](explanation/architecture.md#code-hosts-forges) for the details.

## For AI agents

The site root publishes a curated [`llms.txt`](llms.txt) index of these docs,
plus a whole-corpus [`llms-full.txt`](llms-full.txt), for LLM crawlers and
coding agents.
