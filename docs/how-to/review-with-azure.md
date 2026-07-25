---
description: Review pull requests with Azure OpenAI in lgtmaybe using keyless GitHub OIDC federated to Entra, or an API key.
---

# Review with Azure OpenAI (keyless OIDC)

Use this guide to run lgtmaybe against **Azure OpenAI** using GitHub's OIDC token
federated to Entra (Azure AD) — no static Azure key stored in secrets. A
key-based fallback is covered at the end.

## Contents

- [How it works](#how-it-works)
- [One-time Azure setup](#one-time-azure-setup)
- [Workflow example](#workflow-example)
- [Choosing the model name](#choosing-the-model-name)
- [Running locally with ambient Azure credentials](#running-locally-with-ambient-azure-credentials)
- [Key-based alternative](#key-based-alternative)
- [Troubleshooting](#troubleshooting)

## How it works

GitHub Actions issues a short-lived OIDC token. Entra (Azure AD) exchanges it
for an access token scoped to your Azure OpenAI resource, via a **federated
credential** on an app registration or managed identity. The action does that
exchange for you (pass `azure_client_id` / `azure_tenant_id`), and lgtmaybe
picks up the ambient token through `azure-identity`'s `DefaultAzureCredential`.
No `AZURE_API_KEY` in your secrets.

Azure still needs two non-secret values: the **deployment name** (`model`) and
the **resource endpoint** (`api_base`, `https://<resource>.openai.azure.com`).

## One-time Azure setup

This is the human-only part — do it once in your Azure tenant:

1. Register an **Entra app** (or use a user-assigned **managed identity**). Note
   its **client ID** (`azure_client_id`) and your **tenant ID**
   (`azure_tenant_id`).
2. Add a **federated credential** to it for GitHub Actions:
   - Issuer: `https://token.actions.githubusercontent.com`
   - Subject: scope it to your repo, e.g.
     `repo:<org>/<repo>:pull_request` (or `:ref:refs/heads/main`, `:environment:…`)
   - Audience: `api://AzureADTokenExchange`
3. On the **Azure OpenAI resource**, grant the app the
   **Cognitive Services OpenAI User** role (least privilege — it can call models,
   not manage the resource).
4. **Deploy** the model you want and note the **deployment name** — that is what
   you pass as `model`, not the upstream OpenAI model id.
5. Note the resource **endpoint** (`https://<resource>.openai.azure.com`) — it
   becomes `api_base`. No static key is ever stored.

## Workflow example

The action performs the OIDC exchange for you — no separate `azure/login` step
needed. `id-token: write` is required so GitHub will mint the OIDC token.

```yaml
name: lgtmaybe

on:
  pull_request_target:
  issue_comment:
    types: [created]

permissions:
  id-token: write          # required for the OIDC token exchange (keyless)
  pull-requests: write     # required to post review comments
  contents: read

jobs:
  review:
    # Only trusted authors (repo owner / org member / collaborator) can trigger
    # a review, so fork PRs and drive-by comments cannot spend your provider
    # budget. A maintainer can still opt in to an external PR with /review.
    if: >-
      (github.event_name == 'pull_request_target' &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
      (github.event.issue.pull_request &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: MattJColes/lgtmaybe@v1
        with:
          provider: azure
          model: my-gpt-4o-deployment            # your deployment name
          api_base: ${{ secrets.AZURE_API_BASE }} # https://<resource>.openai.azure.com
          azure_client_id: ${{ secrets.AZURE_CLIENT_ID }}
          azure_tenant_id: ${{ secrets.AZURE_TENANT_ID }}
```

## Choosing the model name

For Azure, `model` is the **deployment name** you chose in the portal, not the
underlying OpenAI model id. A deployment of `gpt-4o` named `my-gpt-4o-deployment`
is referenced as `model: my-gpt-4o-deployment`. litellm routes it as
`azure/<deployment-name>`.

## Running locally with ambient Azure credentials

Keyless works locally too. Install the azure extra, sign in (or use a managed
identity), set the endpoint, and review your current branch's changes:

```bash
pip install 'lgtmaybe[azure]'
az login

export AZURE_API_BASE="https://<resource>.openai.azure.com"

lgtmaybe review \
  --provider azure \
  --model my-gpt-4o-deployment
```

`DefaultAzureCredential` finds your `az login` session (or a managed identity, or
`AZURE_*` env vars) and lgtmaybe never stores a static key.

## Key-based alternative

If you would rather use a resource key, set `AZURE_API_KEY` and `AZURE_API_BASE`
(no `id-token` permission, no `azure_client_id` needed). The `azure-identity`
extra is not required in this mode.

```yaml
      - uses: MattJColes/lgtmaybe@v1
        with:
          provider: azure
          model: my-gpt-4o-deployment
          api_key: ${{ secrets.AZURE_API_KEY }}
          api_base: ${{ secrets.AZURE_API_BASE }}
```

Locally: `export AZURE_API_KEY=… AZURE_API_BASE=…` (or pass `--api-key` /
`--api-base`).

## Troubleshooting

**`azure requires credentials`** — neither a key nor an ambient AD credential was
found. For keyless, check `id-token: write` is present and the federated
credential's subject matches the triggering ref/event. For key mode, set
`AZURE_API_KEY`.

**`azure requires the resource endpoint`** — set `api_base` (or `AZURE_API_BASE`)
to `https://<resource>.openai.azure.com`.

**`keyless azure needs the azure-identity package`** — running keyless on the CLI
without the extra; install `lgtmaybe[azure]`. (The Action image already bundles
it.)

**`AADSTS70021` / no matching federated identity** — the federated credential's
issuer/subject/audience don't match this workflow. Audience must be
`api://AzureADTokenExchange` and the subject must match the repo and event.

**`DeploymentNotFound` / 404** — `model` must be the **deployment name** on the
resource that `api_base` points at, not the upstream OpenAI model id.

**`Unsupported API version`** — pin one by setting `AZURE_API_VERSION`
(e.g. `2024-08-01-preview`) in the environment; litellm otherwise uses a default.
