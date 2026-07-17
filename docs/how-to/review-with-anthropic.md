---
description: Review pull requests with Claude (Anthropic) in lgtmaybe — API-key setup, Claude model choice, and Action or local usage.
---

# Review with Claude (Anthropic)

Anthropic is a key-based provider: add an `ANTHROPIC_API_KEY` and pick a Claude
model. No OIDC, no endpoint to configure.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [Want it through a gateway instead?](#want-it-through-a-gateway-instead)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API key

Create a key in the [Anthropic Console](https://console.anthropic.com/). In your
repository, add it as an Actions secret named `ANTHROPIC_API_KEY`
(**Settings → Secrets and variables → Actions → New repository secret**).

## GitHub Action

Copy [`examples/workflows/review-anthropic.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. The core step is:

```yaml
- uses: MattJColes/lgtmaybe@v0
  with:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

That review runs on `pull_request_target`, so the secret is available while PR
code is **never** checked out. lgtmaybe only reads the diff via the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run locally

```bash
export ANTHROPIC_API_KEY=sk-ant-...

lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

You can pass the key inline with `--api-key sk-ant-...` instead of the env var.
The key is read from the environment or the flag and is **never persisted** to
config.

## Choosing the model

Pass litellm's native Anthropic name as the model. `claude-sonnet-4-6` is a
strong default. Reach for an Opus-class model when you want the deepest review
and are happy to pay more, or a Haiku-class model to cut cost on smaller PRs.

## Want it through a gateway instead?

If you route Claude through OpenRouter or a Bedrock/Vertex deployment rather than
the Anthropic API directly, use that provider instead:

- [OpenRouter](review-with-openrouter.md) — `anthropic/claude-sonnet-4-6`
- [Bedrock (keyless OIDC)](review-with-bedrock-oidc.md)
- [Vertex (keyless WIF)](review-with-vertex-wif.md)

## Persist non-secret defaults

```yaml
provider: anthropic
model: claude-sonnet-4-6
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every knob.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-anthropic.yml
