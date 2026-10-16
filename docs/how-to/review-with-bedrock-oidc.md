---
description: Run lgtmaybe on AWS Bedrock with keyless GitHub OIDC - no static AWS credentials stored in repository secrets.
---

# Review with AWS Bedrock (Keyless OIDC)

Use this guide to run lgtmaybe with AWS Bedrock using GitHub's
[OIDC](../reference/glossary.md#oidc) token. No static AWS credentials are stored in secrets.

## Contents

- [How it works](#how-it-works)
- [One-time AWS setup](#one-time-aws-setup)
- [Workflow example](#workflow-example)
- [Choosing a Bedrock model ID](#choosing-a-bedrock-model-id)
- [Running locally with ambient AWS credentials](#running-locally-with-ambient-aws-credentials)
- [Troubleshooting](#troubleshooting)

## How It Works

GitHub Actions issues a short-lived OIDC token. AWS STS exchanges it for
temporary credentials for an [IAM](../reference/glossary.md#iam) role you control. Pass
`aws_role_arn` and the Action performs that exchange for you. lgtmaybe then
picks up the credentials through the standard AWS credential chain. There is no
`AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` in your secrets.

## One-Time AWS Setup

Do this once in your AWS account:

1. Create an IAM OIDC identity provider for `token.actions.githubusercontent.com`.
2. Create an IAM role with a trust policy scoped to your repository
   (`repo:<org>/<repo>:*`).
3. Attach the least-privilege policy below.
4. Confirm the models you want are enabled in the target region (model access
   in the Bedrock console).
5. Note the role ARN (e.g. `arn:aws:iam::123456789012:role/lgtmaybe-bedrock`).
   It becomes the `aws_role_arn` Action input.

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

Both ARNs are required for the recommended inference-profile model IDs (the
`us.`/`eu.`/`apac.`-prefixed IDs below). A cross-region inference profile routes
the call to the foundation model in several regions, so the role needs
`bedrock:InvokeModel*` on both the `inference-profile/*` ARN and the underlying
`foundation-model/*` ARN. Granting only one still fails with
`AccessDeniedException`. A bare `anthropic.…` model ID needs only the
`foundation-model/*` ARN. But most current Claude models can only be invoked
through an inference profile (see the model table below).

Once it works, scope each `Resource` to specific model and inference-profile
ARNs for tighter least privilege, e.g.
`arn:aws:bedrock:*:<account-id>:inference-profile/us.anthropic.claude-opus-5-5*`
and `arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-5-5*`.

## Workflow Example

Copy [`examples/workflows/review-bedrock.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. Store the role ARN in an `AWS_ROLE_ARN`
secret. The Action assumes the role itself, so you need no separate
`configure-aws-credentials` step. But the workflow must grant `id-token: write`
so GitHub will mint the OIDC token:

```yaml
permissions:
  contents: read
  pull-requests: write
  id-token: write # required for the OIDC token exchange (keyless auth)
```

The review step is:

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: bedrock
    model: us.anthropic.claude-sonnet-4-6
    aws_role_arn: ${{ secrets.AWS_ROLE_ARN }}
    aws_region: us-east-1
```

`aws_region` defaults to `us-east-1`. The example also carries the
trusted-author guard described in
[who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Choosing a Bedrock Model ID

The `model` must be a Bedrock model identifier. Bedrock hosts Anthropic Claude,
Amazon Nova, Meta Llama, Mistral, Cohere, OpenAI GPT and others. Each has
Bedrock's own ID: `openai.gpt-6-sol`, not OpenAI's `gpt-6-sol`. Bedrock rejects
an ID written for another provider's API with
`The provided model identifier is invalid`. Use one of the Claude IDs below:

| Model | Inference-profile ID (recommended) | Base model ID |
|---|---|---|
| Claude Opus 5.5 | `us.anthropic.claude-opus-5-5` | `anthropic.claude-opus-5-5` |
| Claude Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | `anthropic.claude-sonnet-4-6` |
| Claude Haiku 4.5 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | `anthropic.claude-haiku-4-5-20251001-v1:0` |

Haiku 4.5 keeps its dated `-20251001-v1:0` suffix on Bedrock. Unlike Opus 5.5
and Sonnet 4.6, it has no undated `anthropic.claude-haiku-4-5` ID.

On Bedrock, Claude Opus 4.7 and later (Opus 5.5 included) take lgtmaybe's
findings schema as a forced tool call rather than native structured output. A
forced tool call makes the model answer by calling one tool whose input is the
schema. There is nothing to configure: litellm picks that route from its model
map.

Prefer the inference-profile form. Most current Claude models on Bedrock can
only be invoked through a cross-region inference profile. They cannot run with
on-demand throughput on the bare model ID. So a bare ID often fails with the
same `invalid model identifier` (or `on-demand throughput isn't supported`)
error.

Prefix the ID with the geography that matches `aws_region`:

- `us.` (US);
- `eu.` (Europe);
- `apac.` (Asia Pacific);
- or the narrower `au.` / `jp.`.

A `global.` prefix lets Bedrock route the call to any commercial region. Use the
bare `anthropic.…` ID only where on-demand access to that model is enabled in
your region.

## Running Locally with Ambient AWS Credentials

If your shell has [ambient](../reference/glossary.md#ambient-credentials) AWS credentials (from
`~/.aws`, SSO, or an assumed role), you can review your current branch with
Bedrock. Bedrock request signing needs `boto3`. So install the `bedrock` extra
(the Action image already bundles it):

```bash
pip install 'lgtmaybe[bedrock]'

lgtmaybe review \
  --provider bedrock \
  --model us.anthropic.claude-sonnet-4-6
```

Bedrock always uses the AWS credential chain. lgtmaybe ignores `--api-key` for
it.

> **`No matching distribution found for lgtmaybe[bedrock]`** (`from versions:
> none`): this is not a packaging problem, because the `bedrock` extra is
> published on PyPI. It means `pip` found no version compatible with your
> environment. The usual cause is a Python older than the required 3.11. Check
> with `python --version`, and install under 3.11 or later (e.g. with `pipx` or
> `uv`). If your Python is fine, `pip` can't reach PyPI. Check your network,
> proxy or `--index-url`.

## Troubleshooting

**`ExpiredTokenException`**: the OIDC exchange failed or the role session
expired. Check that the workflow grants `id-token: write` and that the IAM trust
policy names the correct GitHub repository.

**`AccessDeniedException`**: the role lacks `bedrock:InvokeModel` for the
selected model, or the model is not enabled in the Bedrock console for your
account and region. For an inference-profile ID (`us.`/`eu.`/`apac.`-prefixed),
the policy must allow the `inference-profile/*` ARN as well as
`foundation-model/*`.

**`The provided model identifier is invalid`**: the `model` is not a Bedrock
model ID. It is one of two things:

- Another provider's name, such as `gpt-6-sol` or `claude-sonnet-4-6`.
  Bedrock's are `openai.gpt-6-sol` and `us.anthropic.claude-sonnet-4-6`. Pick an
  ID from the table above.
- A model that needs a cross-region inference profile. Prefix it with
  `us.`/`eu.`/`apac.` (e.g. `us.anthropic.claude-sonnet-4-6`) instead of using
  the bare `anthropic.…` ID.

**`output_config.format: Extra inputs are not permitted`**, **`outputConfig`**,
or a `toolConfig` / "doesn't support tool choice" refusal: the model's Converse
route (Bedrock's chat API) doesn't accept the structured-output shape lgtmaybe
sent. Bedrock rejected the request with a 400.

No action is needed. lgtmaybe re-sends the same schema as a forced tool call
(which Converse does implement). The rest of the [lens](../reference/glossary.md#lens)
[fan-out](../reference/glossary.md#fan-out) uses that shape straight away. Only a route that refuses
the tool call too falls back to prompt-instructed JSON, which the lenient parser
still reads. lgtmaybe logs that fallback as a warning naming the model and the
reason.

If every lens fails at once with `every review call failed` on an older
lgtmaybe, upgrade, or pass `--no-structured-output` (`structured_output: false`)
as a stopgap.

**Structured output stepped down after an odd reply**: an empty reply, or one
that parsed to something other than findings, moves the model one rung down to
the forced tool call and keeps the schema. Only a second such reply gives the
schema up. The provider log says which happened:

- `structured output stepped down to a forced tool call` is the middle rung and
  costs nothing.
- `structured output disabled for this model` is the floor. It carries a
  `reason` worth reading, such as `rejected`, `empty-response`,
  `unparseable-output`, `tool-rejected` or `internal-tool-rejected`.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-bedrock.yml
