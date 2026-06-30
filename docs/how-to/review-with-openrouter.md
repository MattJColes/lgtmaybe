# Review with OpenRouter

[OpenRouter](https://openrouter.ai/) is a key-based gateway to many model
vendors behind one API. Add an `OPENROUTER_API_KEY`, then pick any model
OpenRouter offers using its `vendor/model` name.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API key

Create a key at <https://openrouter.ai/keys>. In your repository, add it as an
Actions secret named `OPENROUTER_API_KEY`
(**Settings → Secrets and variables → Actions → New repository secret**).

## GitHub Action

Copy [`examples/workflows/review-openrouter.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. The core step is:

```yaml
- uses: MattJColes/lgtmaybe@v0
  with:
    provider: openrouter
    model: anthropic/claude-sonnet-4-6
    api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

That review runs on `pull_request_target`, so the secret is available while PR
code is **never** checked out — lgtmaybe only reads the diff via the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run locally

```bash
export OPENROUTER_API_KEY=sk-or-...

lgtmaybe review --provider openrouter --model anthropic/claude-sonnet-4-6
```

You can pass the key inline with `--api-key sk-or-...` instead of the env var.
The key is read from the environment or the flag and is **never persisted** to
config.

## Choosing the model

OpenRouter models are named `vendor/model`. Pick whichever fits your budget and
quality bar, for example:

- `anthropic/claude-sonnet-4-6`
- `openai/gpt-5.5`
- `z-ai/glm-4.6`

Browse the full catalogue and per-model pricing at
<https://openrouter.ai/models>.

## Persist non-secret defaults

```yaml
provider: openrouter
model: anthropic/claude-sonnet-4-6
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every knob.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-openrouter.yml
