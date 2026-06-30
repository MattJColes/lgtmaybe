---
description: Set up lgtmaybe pull-request reviews with OpenAI — add OPENAI_API_KEY, pick a model, and run as a GitHub Action or locally.
---

# Review with OpenAI

OpenAI is a key-based provider: add an `OPENAI_API_KEY` and pick a model. No
OIDC, no endpoint to configure — the simplest path to a hosted review.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API key

Create a key at <https://platform.openai.com/api-keys>. In your repository, add
it as an Actions secret named `OPENAI_API_KEY`
(**Settings → Secrets and variables → Actions → New repository secret**).

## GitHub Action

Copy [`examples/workflows/review-openai.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. The core step is:

```yaml
- uses: MattJColes/lgtmaybe@v0
  with:
    provider: openai
    model: gpt-5.5
    api_key: ${{ secrets.OPENAI_API_KEY }}
```

That review runs on `pull_request_target`, so the secret is available while PR
code is **never** checked out — lgtmaybe only reads the diff via the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run locally

```bash
export OPENAI_API_KEY=sk-...

lgtmaybe review --provider openai --model gpt-5.5
```

You can pass the key inline with `--api-key sk-...` instead of the env var. The
key is read from the environment or the flag and is **never persisted** to
config.

## Choosing the model

Use any chat model your key can access — e.g. `gpt-5.5` for the strongest
reviews or a smaller/cheaper model to cut cost. Pass the model litellm's native
OpenAI name (the same string you'd send to the API).

## Persist non-secret defaults

Provider and model are non-secret, so they can live in `.lgtmaybe.yml` (the key
stays in the environment):

```yaml
provider: openai
model: gpt-5.5
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every knob.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-openai.yml
