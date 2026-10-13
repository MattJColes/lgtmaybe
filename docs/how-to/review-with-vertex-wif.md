---
description: Run lgtmaybe on Google Vertex AI with keyless Workload Identity Federation - no service-account JSON in secrets.
---

# Review with Google Vertex AI (Keyless WIF)

Use this guide to run lgtmaybe with Google Vertex AI using Workload Identity
Federation (WIF), so no service-account JSON or static key is stored in secrets.

## Contents

- [How it works](#how-it-works)
- [One-time GCP setup](#one-time-gcp-setup)
- [Workflow example](#workflow-example)
- [Environment variables](#environment-variables)
- [Available Vertex AI models](#available-vertex-ai-models)
- [Running locally with ADC](#running-locally-with-adc)
- [Troubleshooting](#troubleshooting)

## How It Works

GitHub Actions issues an OIDC token. WIF exchanges it for a short-lived GCP
access token that impersonates a service account holding only the Vertex AI
permissions it needs. lgtmaybe picks up those credentials through Application
Default Credentials (ADC).

## One-Time GCP Setup

Do this once in your GCP project:

1. Enable the Vertex AI API on the project.
2. Create a workload identity pool and a GitHub provider in it.
3. Create a service account with `roles/aiplatform.user`, or something narrower.
   That role grants `aiplatform.endpoints.predict`; do not assign broader
   project-level roles.
4. Grant the GitHub principal permission to impersonate that service account,
   scoped to your repository.
5. Note the WIF provider resource name (for `gcp_wif_provider`) and the service
   account email (for `gcp_service_account`).

## Workflow Example

Copy [`examples/workflows/review-vertex.yml`][wf] to
`.github/workflows/lgtmaybe.yml` and store the provider resource name and service
account email in `GCP_WIF_PROVIDER` and `GCP_SERVICE_ACCOUNT` secrets. The Action
authenticates to GCP itself, so you need no separate
`google-github-actions/auth` step, but the workflow must grant `id-token: write`:

```yaml
permissions:
  contents: read
  pull-requests: write
  id-token: write # required for the WIF token exchange (keyless auth)
```

The review step is:

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: vertex
    model: gemini-3.8-flash
    gcp_wif_provider: ${{ secrets.GCP_WIF_PROVIDER }}
    gcp_service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}
```

The example also carries the trusted-author guard described in
[who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Environment Variables

litellm reads the project and region from these variables:

| Variable | Required | Description |
|---|---|---|
| `VERTEXAI_PROJECT` | No | GCP project ID. Defaults to the project your ADC resolves (`GOOGLE_CLOUD_PROJECT`, or your gcloud config). |
| `VERTEXAI_LOCATION` | No | Region (default: `us-central1`) |

In the Action, the authentication step sets the project for you. The Action
forwards only `GOOGLE_*` and `CLOUDSDK_*` variables (plus `AWS_*`, `AZURE_*` and
its own `INPUT_*`) into its container, so `VERTEXAI_*` variables set in your
workflow do not reach lgtmaybe there. The Action therefore always uses
litellm's default region, `us-central1`; to use another region, run the CLI in
a workflow step with `VERTEXAI_LOCATION` set instead.

## Available Vertex AI Models

| Model | Vertex model ID |
|---|---|
| Gemini 3.8 Flash | `gemini-3.8-flash` |
| Gemini 3.5 Flash | `gemini-3.5-flash` |
| Gemini 3.1 Pro (preview) | `gemini-3.1-pro-preview` |
| Gemini 3 Pro (preview) | `gemini-3-pro-preview` |
| Gemini 2.5 Pro | `gemini-2.5-pro` |
| Claude Opus 5.5 | `claude-opus-5-5` |

Use the Gemini 3.x Pro `-preview` IDs as written: litellm's model map has no
bare `gemini-3-pro` or `gemini-3.1-pro` entry, so with those lgtmaybe can't fit
its input budget to the model's window. Claude on Vertex must be enabled in
Model Garden first, and `VERTEXAI_LOCATION` must be a region that serves it.

## Running Locally with ADC

Sign in with `gcloud auth application-default login` so your shell has ADC.
Vertex token minting needs `google-auth`, so install the `vertex` extra (the
Action image already bundles it):

```bash
pip install 'lgtmaybe[vertex]'

export VERTEXAI_PROJECT=my-project
export VERTEXAI_LOCATION=us-central1

lgtmaybe review \
  --provider vertex \
  --model gemini-3.8-flash
```

This reviews your current branch with Vertex. Vertex always uses ambient
credentials; lgtmaybe ignores `--api-key` for it.

## Troubleshooting

**`vertex requires ambient GCP credentials`**: lgtmaybe found no sign of GCP
credentials (`GOOGLE_APPLICATION_CREDENTIALS`, a project variable such as
`GOOGLE_CLOUD_PROJECT` or `VERTEXAI_PROJECT`, or the ADC file gcloud writes). Run `gcloud auth application-default login`, set
`GOOGLE_APPLICATION_CREDENTIALS`, or (in CI) check the WIF inputs.

**`UNAUTHENTICATED`**: ADC credentials are missing or expired. Run
`gcloud auth application-default login` locally, or check the WIF provider and
the service account impersonation binding in CI.

**`PERMISSION_DENIED`**: the service account lacks `roles/aiplatform.user`, or
the Vertex AI API is not enabled in the project. Enable it with:

```bash
gcloud services enable aiplatform.googleapis.com --project=my-project
```

**`Model not found`**: the model ID may not be available in your selected
region. Check the [Vertex AI Model Garden](https://console.cloud.google.com/vertex-ai/model-garden).

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-vertex.yml
