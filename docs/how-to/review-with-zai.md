---
description: Configure lgtmaybe to review pull requests with z.ai GLM models through litellm's native zai route.
---

# Review with z.ai (GLM)

[z.ai](https://z.ai/)'s GLM models (from Zhipu AI) are a first-class provider:
add a `ZAI_API_KEY` and pick a model. lgtmaybe reaches them through litellm's
native `zai/` route, so the international endpoint needs no `api_base`.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [China / coding-plan endpoint](#china-coding-plan-endpoint)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API Key

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

The workflow runs on `pull_request_target`, so the secret is available, but PR
code is never checked out: lgtmaybe reads the diff through the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run Locally

```bash
export ZAI_API_KEY=...

lgtmaybe review --provider zai --model glm-4.6
```

You can pass the key with `--api-key ...` instead of the env var. Either way, the
key is never persisted to config.

## Choosing the Model

Pass any GLM chat model your key can access, such as `glm-4.6`, `glm-4.7`,
`glm-4.5-air`, or a newer `glm-5.x`. Use the name z.ai's API expects; lgtmaybe
adds the `zai/` route prefix for you.

## China / Coding-Plan Endpoint

The native route targets z.ai's international endpoint. To use the China or
coding-plan endpoint instead, point `--api-base` (or `ZAI_API_BASE`, or the
Action's `api_base` input) at it:

```bash
lgtmaybe review \
  --provider zai \
  --model glm-4.6 \
  --api-base https://open.bigmodel.cn/api/paas/v4
```

## Persist Non-Secret Defaults

Provider and model are not secrets, so they can live in `.lgtmaybe.yml`. The key
stays in the environment.

```yaml
provider: zai
model: glm-4.6
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every setting.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-zai.yml
