---
description: Run lgtmaybe on AWS Bedrock with keyless GitHub OIDC — no static AWS credentials stored in repository secrets.
---

# Review with AWS Bedrock (keyless OIDC)

Use this guide to run lgtmaybe with **AWS Bedrock** using GitHub's OIDC token
— no static AWS credentials stored in secrets.

## Contents

- [How it works](#how-it-works)
- [One-time AWS setup](#one-time-aws-setup)
- [Workflow example](#workflow-example)
- [Choosing a Bedrock model ID](#choosing-a-bedrock-model-id)
- [Running locally with ambient AWS credentials](#running-locally-with-ambient-aws-credentials)
- [Troubleshooting](#troubleshooting)

## How it works

GitHub Actions issues a short-lived OIDC token. AWS STS exchanges that token
for temporary IAM credentials scoped to a role you control. The action performs
that exchange for you (pass `aws_role_arn`) and lgtmaybe picks up the ambient
credentials automatically — no `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY`
in your secrets.

## One-time AWS setup

This is the human-only part — do it once in your AWS account:

1. Create an IAM **OIDC identity provider** for `token.actions.githubusercontent.com`.
2. Create an IAM **role** with a trust policy scoped to your repo
   (`repo:<org>/lgtmaybe:*`).
3. Attach the least-privilege policy below.
4. Confirm the models you want are enabled in the target region (model access
   request in the Bedrock console).
5. Note the **role ARN** (e.g. `arn:aws:iam::123456789012:role/lgtmaybe-bedrock`)
   — it becomes the `aws_role_arn` action input. No static key is ever stored.

The role needs only:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream"
  ],
  "Resource": [
    "arn:aws:bedrock:*:<account-id>:inference-profile/*",
    "arn:aws:bedrock:*::foundation-model/*"
  ]
}
```

**Both ARNs are required for the recommended inference-profile model ids**
(the `us.`/`eu.`/`apac.`-prefixed ids below). A cross-region inference profile
fans the call out to the foundation model in several regions, so the role needs
`bedrock:InvokeModel*` on **both** the `inference-profile/*` ARN **and** the
underlying `foundation-model/*` ARN. Granting only one of them still fails with
`AccessDeniedException`. A bare `anthropic.…` model id needs only the
`foundation-model/*` ARN, but most current Claude models are invocable only
through an inference profile — see the model table below.

Scope each `Resource` to specific model / inference-profile ARNs for tighter
least-privilege once it works, e.g.
`arn:aws:bedrock:*:<account-id>:inference-profile/us.anthropic.claude-opus-4-8*`
and `arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-4-8*`.

## Workflow example

The action assumes the role for you — no separate `configure-aws-credentials`
step needed. Store the role ARN in an `AWS_ROLE_ARN` secret.

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
    if: ${{ github.event_name == 'pull_request_target' || github.event.issue.pull_request }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: MattJColes/lgtmaybe@v0
        with:
          provider: bedrock
          model: us.anthropic.claude-sonnet-4-6
          aws_role_arn: ${{ secrets.AWS_ROLE_ARN }}
          aws_region: us-east-1
```

## Choosing a Bedrock model ID

The `model` must be a **Bedrock** model identifier. Bedrock hosts Anthropic
Claude, Amazon Nova/Titan, Meta Llama, Mistral, Cohere and others — it does
**not** host OpenAI's GPT models, so an id like `openai.gpt-5.5` is rejected with
`The provided model identifier is invalid`. Use one of the Claude ids below
(prefixed with a cross-region inference profile — see the note):

| Model | Inference-profile ID (recommended) | Base model ID |
|---|---|---|
| Claude Opus 4.8 | `us.anthropic.claude-opus-4-8` | `anthropic.claude-opus-4-8` |
| Claude Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | `anthropic.claude-sonnet-4-6` |
| Claude Haiku 4.5 | `us.anthropic.claude-haiku-4-5` | `anthropic.claude-haiku-4-5` |

**Prefer the inference-profile form.** Most current Claude models on Bedrock are
invocable only through a cross-region inference profile, not via on-demand
throughput on the bare model id — so a bare id often fails with the same
`invalid model identifier` (or `on-demand throughput isn't supported`) error.
Prefix with your geography: `us.` (US), `eu.` (Europe) or `apac.` (Asia
Pacific), matching `aws_region`. Use the bare `anthropic.…` id only where
on-demand access to that model is enabled in your region.

## Running locally with ambient AWS credentials

If your local shell has AWS credentials (via `~/.aws`, SSO, or an assumed role),
you can review your current branch's changes with Bedrock. Bedrock signing needs
`boto3`, so install the extra (the Action image already bundles it):

```bash
pip install 'lgtmaybe[bedrock]'

lgtmaybe review \
  --provider bedrock \
  --model us.anthropic.claude-sonnet-4-6
```

lgtmaybe does not require or accept a static API key for Bedrock.

> **`No matching distribution found for lgtmaybe[bedrock]`** (`from versions:
> none`) — this is not a packaging problem; the `bedrock` extra is published on
> PyPI. It means `pip` found no version compatible with your environment. The
> usual cause is an interpreter older than the required **Python 3.11+** (check
> with `python --version`); install under 3.11+ (e.g. via `pipx`/`uv`). If your
> Python is fine, your `pip` can't reach PyPI — check your network/proxy or
> `--index-url`.

## Troubleshooting

**`ExpiredTokenException`** — the OIDC exchange failed or the role session
expired. Check that `id-token: write` permission is present in the workflow and
that the IAM trust policy references the correct GitHub repository.

**`AccessDeniedException`** — the role lacks `bedrock:InvokeModel` for the
selected model, or the model is not enabled in the Bedrock console for your
account and region. For an inference-profile id (`us.`/`eu.`/`apac.`-prefixed),
the policy must also allow the `inference-profile/*` ARN, not just
`foundation-model/*`.

**`The provided model identifier is invalid`** — the `model` is not a Bedrock
model id. Two common causes: (1) it's a non-Bedrock id such as `openai.gpt-5.5`
or another provider's name — Bedrock hosts Claude / Nova / Llama / Mistral /
Cohere, not OpenAI GPT, so pick a Claude id from the table above; (2) the model
needs a cross-region inference profile — prefix with `us.`/`eu.`/`apac.` (e.g.
`us.anthropic.claude-sonnet-4-6`) rather than the bare `anthropic.…` id.
