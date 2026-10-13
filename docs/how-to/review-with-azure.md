---
description: Review pull requests with Azure OpenAI in lgtmaybe using keyless GitHub OIDC federated to Entra, or an API key.
---

# Review with Azure OpenAI (Keyless OIDC)

Use this guide to run lgtmaybe against Azure OpenAI with GitHub's OIDC token
federated to Entra (Azure AD), so no static Azure key is stored in secrets. A
key-based alternative is covered at the end.

## Contents

- [How it works](#how-it-works)
- [One-time Azure setup](#one-time-azure-setup)
- [Workflow example](#workflow-example)
- [Choosing the model name](#choosing-the-model-name)
- [Running locally with ambient Azure credentials](#running-locally-with-ambient-azure-credentials)
- [Key-based alternative](#key-based-alternative)
- [Troubleshooting](#troubleshooting)

## How It Works

GitHub Actions issues a short-lived OIDC token. Entra exchanges it for an access
token for your Azure OpenAI resource, through a federated credential on an app
registration or managed identity. Pass `azure_client_id` and `azure_tenant_id`
and the Action sets up that exchange for you; lgtmaybe then picks up the token
through `azure-identity`'s `DefaultAzureCredential`. There is no
`AZURE_API_KEY` in your secrets.

Azure still needs two non-secret values: the deployment name (`model`) and the
resource endpoint (`api_base`, `https://<resource>.openai.azure.com`).

## One-Time Azure Setup

Do this once in your Azure tenant:

1. Register an Entra app (or use a user-assigned managed identity). Note its
   client ID (`azure_client_id`) and your tenant ID (`azure_tenant_id`).
2. Add a federated credential to it for GitHub Actions:
   - Issuer: `https://token.actions.githubusercontent.com`
   - Subject: scoped to your repo, e.g. `repo:<org>/<repo>:pull_request`
     (or `:ref:refs/heads/main`, `:environment:…`)
   - Audience: `api://AzureADTokenExchange`
3. On the Azure OpenAI resource, grant the app the
   **Cognitive Services OpenAI User** role. It is least privilege: it can call
   models but not manage the resource.
4. Deploy the model you want and note the deployment name. That is what you
   pass as `model`, not the upstream OpenAI model ID.
5. Note the resource endpoint (`https://<resource>.openai.azure.com`). It
   becomes `api_base`.

## Workflow Example

Copy [`examples/workflows/review-azure.yml`][wf] to
`.github/workflows/lgtmaybe.yml` and set the `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID` and `AZURE_API_BASE` secrets. The Action performs the OIDC
exchange itself, so you need no separate `azure/login` step, but the workflow
must grant `id-token: write` so GitHub will mint the token:

```yaml
permissions:
  id-token: write          # required for the OIDC token exchange (keyless)
  contents: read
  pull-requests: write
```

The review step is:

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: azure
    model: my-gpt-4o-deployment      # your Azure deployment name
    api_base: ${{ secrets.AZURE_API_BASE }}  # https://<resource>.openai.azure.com
    azure_client_id: ${{ secrets.AZURE_CLIENT_ID }}
    azure_tenant_id: ${{ secrets.AZURE_TENANT_ID }}
```

The example also carries the trusted-author guard described in
[who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Choosing the Model Name

For Azure, `model` is the deployment name you chose in the portal, not the
underlying OpenAI model ID. A deployment of `gpt-4o` named `my-gpt-4o-deployment`
is referenced as `model: my-gpt-4o-deployment`, which litellm routes as
`azure/my-gpt-4o-deployment`.

## Running Locally with Ambient Azure Credentials

Keyless works locally too. Install the `azure` extra, sign in (or use a managed
identity), set the endpoint, and review your current branch:

```bash
pip install 'lgtmaybe[azure]'
az login

export AZURE_API_BASE="https://<resource>.openai.azure.com"

lgtmaybe review \
  --provider azure \
  --model my-gpt-4o-deployment
```

`DefaultAzureCredential` finds your `az login` session (or a managed identity,
or `AZURE_*` env vars), and no static key is stored.

## Key-Based Alternative

To use a resource key instead, pass it as `api_key` with the endpoint as
`api_base`. Drop `azure_client_id`, `azure_tenant_id` and the `id-token`
permission. A supplied key always wins over ambient credentials, and the
`azure-identity` package is not needed in this mode.

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: azure
    model: my-gpt-4o-deployment
    api_key: ${{ secrets.AZURE_API_KEY }}
    api_base: ${{ secrets.AZURE_API_BASE }}
```

Locally, `export AZURE_API_KEY=… AZURE_API_BASE=…` (or pass `--api-key` and
`--api-base`).

## Troubleshooting

**`azure requires credentials`**: lgtmaybe found neither a key nor an ambient
Entra credential. For keyless, check that `id-token: write` is present and the
federated credential's subject matches the triggering ref or event. For key
mode, set `AZURE_API_KEY` (or the `api_key` input).

**`azure requires the resource endpoint`**: set `api_base` (or `AZURE_API_BASE`)
to `https://<resource>.openai.azure.com`.

**`keyless azure needs the azure-identity package`**: you are running keyless on
the CLI without the extra. Install `lgtmaybe[azure]`. The Action image already
bundles it.

**`AADSTS70021` / no matching federated identity**: the federated credential's
issuer, subject or audience don't match this workflow. The audience must be
`api://AzureADTokenExchange` and the subject must match the repo and event.

**`DeploymentNotFound` / 404**: `model` must be the deployment name on the
resource that `api_base` points at, not the upstream OpenAI model ID.

**`Unsupported API version`**: pin one by setting `AZURE_API_VERSION` (e.g.
`2024-08-01-preview`) in the environment. Otherwise litellm uses its default.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-azure.yml
