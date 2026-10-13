---
description: Set up lgtmaybe pull-request reviews with OpenAI - add OPENAI_API_KEY, pick a model, and run as a GitHub Action or locally.
---

# Review with OpenAI

OpenAI is a key-based provider: add an `OPENAI_API_KEY` and pick a model. There
is no OIDC and no endpoint to configure, which makes it the simplest path to a
hosted review.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API Key

Create a key at <https://platform.openai.com/api-keys>. In your repository, add
it as an Actions secret named `OPENAI_API_KEY`
(**Settings → Secrets and variables → Actions → New repository secret**).

## GitHub Action

Copy [`examples/workflows/review-openai.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. The core step is:

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: openai
    model: gpt-5.5
    api_key: ${{ secrets.OPENAI_API_KEY }}
```

The workflow runs on `pull_request_target`, so the secret is available, but PR
code is never checked out: lgtmaybe reads the diff through the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run Locally

```bash
export OPENAI_API_KEY=sk-...

lgtmaybe review --provider openai --model gpt-5.5
```

You can pass the key with `--api-key sk-...` instead of the env var. Either way,
the key is never persisted to config.

## Choosing the Model

Use any chat model your key can access, named as you would send it to the
OpenAI API (litellm's native form): `gpt-5.5` for the strongest reviews, or a
smaller model to cut cost.

Under the default `fast` preset, a `gpt-5*` review model has its reflection
(false-positive audit) pass run on `gpt-5-mini`, so your key needs access to
that model too. Set `reflect_model` to choose a different auditor.

## Persist Non-Secret Defaults

Provider and model are not secrets, so they can live in `.lgtmaybe.yml`. The key
stays in the environment.

```yaml
provider: openai
model: gpt-5.5
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every setting.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-openai.yml
