# Use lgtmaybe as a GitHub Action

Use this guide to add lgtmaybe to a repository as a GitHub Actions workflow
that reviews pull requests automatically.

Ready-to-copy workflows for every cloud and API-key provider live in
[`examples/workflows/`](https://github.com/MattJColes/lgtmaybe/tree/main/examples/workflows).
ollama runs the model on your own machine, so it is local-only — use the
[CLI](run-a-local-model.md) rather than a posting workflow.

## Security requirement: pull_request_target

All lgtmaybe workflows use the `pull_request_target` trigger, not
`pull_request`. This is non-negotiable:

- `pull_request_target` runs in the context of the **base branch**, so it can
  access secrets and write to the PR.
- lgtmaybe **never checks out or executes PR code** — it fetches the diff via
  the GitHub API only. The PR author cannot inject code that runs in the
  reviewer's environment.

The action derives the PR from the triggering event, so there is no `pr-url`
input to set. On an `issue_comment` event it routes the slash command
(`/review`, `/ask`, `/describe`, `/improve`) to the same engine.

> **Note on cost.** With ollama the model runs on your own hardware, so reviews
> are free. On a hosted provider each run uses tokens you pay for, so it's worth
> a moment's thought about who can trigger one (next section) — the default keeps
> that to people you trust, and `max_files` / `max_input_tokens` keep any single
> run modest.

## Who can trigger a review

You choose who reviews run for. The example workflows gate the `review` job on
the triggering user's
[author association](https://docs.github.com/en/graphql/reference/enums#commentauthorassociation)
and default to **trusted contributors** — `OWNER`, `MEMBER`, and `COLLABORATOR`.
A maintainer can also review an outside contributor's PR any time by commenting
`/review` on it (their own association passes the gate).

To change the policy, edit the `if:` on the `review` job:

- **Everyone** — drop the `if:` so any PR or `/ask` / `/review` comment runs a
  review (a friendly choice for an open project; on a hosted provider it means
  anyone can start a run, so pick it deliberately).
- **Returning contributors too** — add `CONTRIBUTOR` to auto-review anyone whose
  PR has merged before.
- **Admins only** — keep just `OWNER` (plus `MEMBER` for your org).

For extra guardrails, you can also require approval for fork-PR workflow runs in
**Settings → Actions → General → Fork pull request workflows**, or move the
provider key behind a protected `environment`. See
[Trust and Cost](../explanation/trust-and-cost.md) for the reasoning behind these
options.

## Minimal workflow — openai

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
    # Only trusted authors (owner / member / collaborator) can trigger a review.
    if: >-
      (github.event_name == 'pull_request_target' &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
      (github.event.issue.pull_request &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6 # base repo only — for .lgtmaybe.yml config
      - uses: MattJColes/lgtmaybe@v0
        with:
          provider: openai
          model: gpt-5.5
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

## Other key-based providers

Swap the `provider`, `model`, and `api_key` inputs:

```yaml
# anthropic
- uses: MattJColes/lgtmaybe@v0
  with:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: ${{ secrets.ANTHROPIC_API_KEY }}

# openrouter
- uses: MattJColes/lgtmaybe@v0
  with:
    provider: openrouter
    model: anthropic/claude-sonnet-4-6
    api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

For these, the one-time setup is just: generate an API key in the provider's
console and add it as a repo secret (Settings → Secrets and variables → Actions),
then reference it as `api_key` above.

## Keyless cloud workflows

Bedrock (AWS OIDC), Vertex (GCP WIF), and Azure (Entra OIDC) need **no API keys
in secrets** — the action performs the keyless token exchange for you when you
pass `aws_role_arn`, `gcp_wif_provider`, or `azure_client_id`. All require
`id-token: write` permission. See:

- [Review with Bedrock OIDC](./review-with-bedrock-oidc.md)
- [Review with Vertex WIF](./review-with-vertex-wif.md)
- [Review with Azure OpenAI](./review-with-azure.md)

## Action inputs

| Input | Default | Description |
|---|---|---|
| `provider` | — | One of: `openai`, `openrouter`, `anthropic`, `bedrock`, `vertex`, `azure`, `ollama` |
| `model` | — | Model identifier for the chosen provider |
| `fallback_model` | — | Model to retry with if the primary model fails |
| `api_key` | — | API key for key-based providers (leave empty for bedrock/vertex/ollama and keyless azure) |
| `api_base` | — | Resource endpoint for azure (`https://<resource>.openai.azure.com`), or a custom base URL for other providers |
| `timeout` | provider default (ollama 300s, cloud 60s) | Per-request timeout in seconds for each model call. Transient failures (capacity 429s, timeouts, 5xx) are retried with exponential backoff; permanent ones (bad key, quota/billing 429, unknown model) fail fast |
| `temperature` | `0.0` | Sampling temperature (0.0 = deterministic) |
| `num_ctx` | `16384` | Ollama context window (ollama only; ignored for hosted providers) |
| `max_input_tokens` | `100000` | Token budget per model call before the diff is split into batches (any provider) |
| `resolve_fixed` | `true` | Auto-resolve a review conversation once its finding is fixed (set `false` to resolve manually) |
| `aws_role_arn` | — | IAM role ARN to assume via OIDC for bedrock (keyless) |
| `aws_region` | `us-east-1` | AWS region for bedrock |
| `gcp_wif_provider` | — | Workload Identity Federation provider resource name for vertex |
| `gcp_service_account` | — | GCP service account email to impersonate via WIF |
| `azure_client_id` | — | Entra (Azure AD) client ID with a federated credential — keyless azure via OIDC |
| `azure_tenant_id` | — | Entra (Azure AD) tenant ID for keyless azure |
| `config_path` | `.lgtmaybe.yml` | Path to the config file, relative to repo root |
| `github_token` | `${{ github.token }}` | Token for reading the PR and posting the review |
| `image` | `ghcr.io/mattjcoles/lgtmaybe:v0` | Override the container image (advanced) |

The action sets the `GITHUB_TOKEN` and provider credentials for the container
itself — you do not pass them as `env`.

## Adding a config file

Place a `.lgtmaybe.yml` at the repo root to control severity thresholds, path
filters, and cost caps. See
[Configure .lgtmaybe.yml](./configure-lgtmaybe-yml.md) for all options.

## Pin to a specific version

`@v0` is a floating tag that tracks the latest `v0.x.x` release. To pin exactly,
use a full version tag:

```yaml
uses: MattJColes/lgtmaybe@v0.1.0
```
