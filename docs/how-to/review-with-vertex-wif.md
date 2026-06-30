# Review with Google Vertex AI (keyless WIF)

Use this guide to run lgtmaybe with **Google Vertex AI** using Workload Identity
Federation — no service-account JSON or static keys in secrets.

## Contents

- [How it works](#how-it-works)
- [One-time GCP setup](#one-time-gcp-setup)
- [Workflow example](#workflow-example)
- [Environment variables](#environment-variables)
- [Available Vertex AI models](#available-vertex-ai-models)
- [Running locally with ADC](#running-locally-with-adc)
- [Troubleshooting](#troubleshooting)

## How it works

GitHub Actions issues an OIDC token. GCP's Workload Identity Federation
exchanges that token for a short-lived GCP access token, impersonating a service
account that has only the Vertex AI permissions it needs. lgtmaybe uses
Application Default Credentials (ADC) to pick up those tokens automatically.

## One-time GCP setup

This is the human-only part — do it once in your GCP project:

1. Enable the Vertex AI API on the project.
2. Create a **workload identity pool** + a GitHub provider in it.
3. Create a **service account** with `roles/aiplatform.user` (or narrower — that
   role grants `aiplatform.endpoints.predict`; do not assign broader project-level
   roles).
4. Grant the GitHub principal permission to impersonate that service account,
   scoped to your repo.
5. Note the **WIF provider resource name** (→ `gcp_wif_provider`) and the
   **service account email** (→ `gcp_service_account`). No key file is ever stored.

## Workflow example

The action authenticates to GCP for you — no separate
`google-github-actions/auth` step needed. Store the provider resource name and
service account email in `GCP_WIF_PROVIDER` and `GCP_SERVICE_ACCOUNT` secrets.

```yaml
name: lgtmaybe

on:
  pull_request_target:
  issue_comment:
    types: [created]

permissions:
  id-token: write          # required for the WIF token exchange (keyless)
  pull-requests: write     # required to post review comments
  contents: read

jobs:
  review:
    if: ${{ github.event_name == 'pull_request_target' || github.event.issue.pull_request }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: MattJColes/lgtmaybe@v0
        with:
          provider: vertex
          model: gemini-3-pro
          gcp_wif_provider: ${{ secrets.GCP_WIF_PROVIDER }}
          gcp_service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `VERTEXAI_PROJECT` | Yes | GCP project ID |
| `VERTEXAI_LOCATION` | No | Region (default: `us-central1`) |

## Available Vertex AI models

| Model | Vertex model ID |
|---|---|
| Gemini 3 Pro | `gemini-3-pro` |
| Gemini 3.1 Pro | `gemini-3.1-pro` |
| Gemini 3 Flash | `gemini-3-flash` |
| Gemini 3.5 Flash | `gemini-3.5-flash` |
| Gemini 2.5 Pro | `gemini-2.5-pro` |

## Running locally with ADC

If your local shell has application default credentials (`gcloud auth
application-default login`). Vertex token minting needs `google-auth`, so install
the extra (the Action image already bundles it):

```bash
pip install 'lgtmaybe[vertex]'

export VERTEXAI_PROJECT=my-project
export VERTEXAI_LOCATION=us-central1

lgtmaybe review \
  --provider vertex \
  --model gemini-3-pro
```

This reviews your current branch's changes with Vertex; lgtmaybe does not accept
a static API key for it.

## Troubleshooting

**`UNAUTHENTICATED`** — ADC credentials are missing or expired. Run
`gcloud auth application-default login` locally, or verify the WIF provider and
service account impersonation binding in CI.

**`PERMISSION_DENIED`** — the service account lacks `roles/aiplatform.user`, or
the Vertex AI API is not enabled in the project. Enable it with:

```bash
gcloud services enable aiplatform.googleapis.com --project=my-project
```

**`Model not found`** — the model ID may not be available in your selected
region. Check the [Vertex AI model garden](https://console.cloud.google.com/vertex-ai/model-garden).
