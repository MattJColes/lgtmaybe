---
description: Use OpenRouter as the lgtmaybe backend to reach many model vendors through one OpenAI-compatible API key.
---

# Review with OpenRouter

[OpenRouter](https://openrouter.ai/) is a key-based gateway to many model
vendors behind one API. Add an `OPENROUTER_API_KEY`, then pick any model
OpenRouter offers using its `vendor/model` name.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [Credit reservations](#credit-reservations)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API key

Create a key at <https://openrouter.ai/keys>. In your repository, add it as an
Actions secret named `OPENROUTER_API_KEY`
(**Settings → Secrets and variables → Actions → New repository secret**).

## GitHub Action

Copy [`examples/workflows/review-openrouter.yml`][wf] to
`.github/workflows/lgtmaybe.yml`. The core step is:

```yaml
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: openrouter
    model: anthropic/claude-sonnet-4-6
    api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

That review runs on `pull_request_target`, so the secret is available while PR
code is **never** checked out. lgtmaybe only reads the diff via the API. See
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

## Credit reservations

OpenRouter checks your balance **before** it generates anything, costing the
worst case: the prompt plus the most tokens the reply could use. A request that
sends no cap is assumed to want the model's full output ceiling, so a review can
be refused for credit it was never going to spend:

```
This request requires more credits, or fewer max_tokens.
You requested up to 65536 tokens, but can only afford 25905.
```

Top up, or cap what each call may generate so the reservation matches a real
findings payload:

```yaml
max_tokens: 8192
```

`--max-tokens 8192` does the same for one run, and `max_tokens` is a GitHub
Action input too. Leave it unset and no cap is sent, which is the safe default:
a cap sized too low truncates the findings JSON mid-object and the call fails to
parse. Reasoning models spend this budget on thinking tokens as well, so give
them more headroom than a plain model.

lgtmaybe treats this refusal as permanent and stops after one attempt — your
balance cannot grow mid-review, so retrying every lens would only waste runner
time before reporting the same failure.

## Rate limits

A *capacity* 429 is different: it is temporary, and lgtmaybe handles it for you.
Rate-limited calls back off on a 5s–60s ladder — long enough to reach a fresh
per-minute window — and honour OpenRouter's own `Retry-After` when it sends one
(clamped at 120s). A lens still failing on the provider when the fan-out drains
is re-run once more. That rescue is only for provider-side failures: unparseable
output, a blown `max_tokens` ceiling, a batch the oversized-diff split already
retried, an unrecoverable failure like a spent quota, and any ceiling you set
(`max_review_seconds`, `max_review_tokens`, a cancelled job) are all left alone,
because a second attempt would buy the same answer at full price. You will
usually see none of this; a review that could not recover says so in its
summary, and names the lens it lost.

If you *are* seeing rate limits, the review's own burst is worth a look before
the model is. Every `(batch, lens)` call shares one concurrency pool and one API
key, so a wide fan-out can meter itself:

```yaml
max_concurrency: 3
```

Beyond that it is an account question rather than a lgtmaybe one. OpenRouter's
limits on **free** model variants (the `:free` suffix) are account-wide rather
than per-model, and the daily allowance depends on how much credit the account
has bought — so the same review is far more likely to be throttled on a free
variant than a paid one. **Paid** models carry no OpenRouter platform request
cap, though the upstream provider behind a given model can still rate-limit you.
Neither is the same thing as a per-key credit *spending* cap, which limits what a
key may spend, not how often it may call.

## Persist non-secret defaults

```yaml
provider: openrouter
model: anthropic/claude-sonnet-4-6
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every knob.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-openrouter.yml
