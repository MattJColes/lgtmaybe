---
description: Configure lgtmaybe to review pull requests with z.ai GLM models through litellm's native zai route.
---

# Review with z.ai (GLM)

[z.ai](https://z.ai/)'s GLM models (GLM-4.6 and friends, from Zhipu AI) are a
first-class provider: add a `ZAI_API_KEY` and pick a model. lgtmaybe reaches them
through litellm's native `zai/` route — no `api_base` needed for the
international endpoint.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [China / coding-plan endpoint](#china-coding-plan-endpoint)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API key

Create a key in the [z.ai developer console](https://z.ai/) (see the
[API docs](https://docs.z.ai/)). In your repository, add it as an Actions secret
named `ZAI_API_KEY`
(**Settings → Secrets and variables → Actions → New repository secret**).

## GitHub Action

Copy [`examples/workflows/review-zai.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. The core step is:

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: zai
    model: glm-4.6
    api_key: ${{ secrets.ZAI_API_KEY }}
```

That review runs on `pull_request_target`, so the secret is available while PR
code is **never** checked out. lgtmaybe only reads the diff via the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run locally

```bash
export ZAI_API_KEY=...

lgtmaybe review --provider zai --model glm-4.6
```

You can pass the key inline with `--api-key ...` instead of the env var. The key
is read from the environment or the flag and is **never persisted** to config.

## Choosing the model

Pass any GLM chat model your key can access — e.g. `glm-4.6`, `glm-4.7`,
`glm-4.5-air`, or a newer `glm-5.x`. Use the model name as z.ai's API expects it
(litellm prefixes the `zai/` route for you).

## China / coding-plan endpoint

The native route targets z.ai's **international** endpoint. To use the China or
coding-plan endpoint instead, point `--api-base` (or `ZAI_API_BASE`, or the
Action's `api_base` input) at it:

```bash
lgtmaybe review \
  --provider zai \
  --model glm-4.6 \
  --api-base https://open.bigmodel.cn/api/paas/v4
```

## Persist non-secret defaults

```yaml
provider: zai
model: glm-4.6
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every knob.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-zai.yml
