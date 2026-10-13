---
description: Review pull requests with Claude (Anthropic) in lgtmaybe - API-key setup, Claude model choice, and Action or local usage.
---

# Review with Claude (Anthropic)

Anthropic is a key-based provider: add an `ANTHROPIC_API_KEY` and pick a Claude
model. There is no OIDC and no endpoint to configure.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [Want it through a gateway instead?](#want-it-through-a-gateway-instead)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API Key

Create a key in the [Anthropic Console](https://console.anthropic.com/). In your
repository, add it as an Actions secret named `ANTHROPIC_API_KEY`
(**Settings → Secrets and variables → Actions → New repository secret**).

## GitHub Action

Copy [`examples/workflows/review-anthropic.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. The core step is:

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The workflow runs on `pull_request_target`, so the secret is available, but PR
code is never checked out: lgtmaybe reads the diff through the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run Locally

```bash
export ANTHROPIC_API_KEY=sk-ant-...

lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

You can pass the key with `--api-key sk-ant-...` instead of the env var. Either
way, the key is never persisted to config.

## Choosing the Model

Pass litellm's native Anthropic name as the model. `claude-sonnet-4-6` is a
strong default. Use an Opus-class model (such as `claude-opus-4-8`) for the
deepest review at a higher price, or a Haiku-class model to cut cost on smaller
PRs.

Under the default `fast` preset, a Sonnet or Opus review model has its
reflection (false-positive audit) pass run on `claude-haiku-4-5`, so your key
needs access to that model too. Set `reflect_model` to choose a different
auditor.

## Want It Through a Gateway Instead?

If you reach Claude through OpenRouter, Bedrock or Vertex rather than the
Anthropic API, use that provider instead:

- [OpenRouter](review-with-openrouter.md): `anthropic/claude-sonnet-4-6`
- [Bedrock (keyless OIDC)](review-with-bedrock-oidc.md)
- [Vertex (keyless WIF)](review-with-vertex-wif.md)

## Persist Non-Secret Defaults

Provider and model are not secrets, so they can live in `.lgtmaybe.yml`. The key
stays in the environment.

```yaml
provider: anthropic
model: claude-sonnet-4-6
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every setting.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-anthropic.yml
