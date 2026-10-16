---
description: Use OpenRouter as the lgtmaybe backend to reach many model vendors through one OpenAI-compatible API key.
---

# Review with OpenRouter

[OpenRouter](https://openrouter.ai/) is a key-based gateway to many model
vendors behind one API. Add an `OPENROUTER_API_KEY`, then pick any model
OpenRouter offers by its `vendor/model` name.

## Contents

- [Get an API key](#get-an-api-key)
- [GitHub Action](#github-action)
- [Run locally](#run-locally)
- [Choosing the model](#choosing-the-model)
- [Credit reservations](#credit-reservations)
- [Rate limits](#rate-limits)
- [Persist non-secret defaults](#persist-non-secret-defaults)

## Get an API Key

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
    model: anthropic/claude-sonnet-4.6
    api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

The workflow runs on `pull_request_target`, so the secret is available, but PR
code is never checked out: lgtmaybe reads the diff through the API. See
[Use as a GitHub Action](use-as-github-action.md) for the full workflow,
including [who can trigger a review](use-as-github-action.md#who-can-trigger-a-review).

## Run Locally

```bash
export OPENROUTER_API_KEY=sk-or-...

lgtmaybe review --provider openrouter --model anthropic/claude-sonnet-4.6
```

You can pass the key with `--api-key sk-or-...` instead of the env var. Either
way, the key is never persisted to config.

## Choosing the Model

OpenRouter models are named `vendor/model`. Pick whichever fits your budget and
quality bar, for example:

- `anthropic/claude-sonnet-4.6`
- `openai/gpt-5.5`
- `z-ai/glm-4.6`

Browse the full catalogue and per-model pricing at
<https://openrouter.ai/models>.

One catalogue quirk: a model can accept JSON-schema mode through OpenRouter yet
return replies that parse as empty. The review then comes back with zero
findings and no error (seen with `anthropic/claude-fable-5.1`). If a model is
silent on everything, try one diff with `--no-structured-output` (see
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md#structured_output)) before
writing the model off.

## Credit Reservations

OpenRouter checks your balance before it generates anything. It costs the
worst case: the prompt plus the most tokens the reply could use. OpenRouter
assumes a request with no cap wants the model's full output ceiling. So it can
refuse a review for credit the review was never going to spend:

```
This request requires more credits, or fewer max_tokens.
You requested up to 65536 tokens, but can only afford 25905.
```

To keep that reservation realistic, OpenRouter reviews send a default cap of
16384 tokens per call. If a refusal names a larger number, something has
overridden the default. Check the `per-call budget resolved` log line, which
names the ceiling and where it came from:

```
per-call budget resolved  timeout_s=1800  max_tokens=16384  max_tokens_source="provider default"
```

Top up, or lower the cap so the reservation fits your balance:

```yaml
max_tokens: 8192
```

`--max-tokens 8192` does the same for one run. `max_tokens` is also a GitHub
Action input.

Lower it with care:

- A cap that is too low [truncates](../reference/glossary.md#truncation) the
  findings JSON mid-object.
- Reasoning models spend this same budget on thinking tokens, so they need more
  headroom than a plain model.

`max_tokens: 0` removes the cap and puts the reservation back to the model's
full ceiling.

lgtmaybe treats this refusal as permanent and stops after one attempt. Your
balance cannot grow mid-review, so retrying every
[lens](../reference/glossary.md#lens) would only waste runner time before
reporting the same failure.

## Rate Limits

A capacity 429 (the HTTP status for too many requests) is different. It is
temporary, and lgtmaybe handles it for you:

- Rate-limited calls back off on a 20s-60s ladder. That is long enough to reach
  a fresh per-minute window.
- They honour OpenRouter's `Retry-After` header when it sends one (clamped at
  120s).
- A lens that is still failing on the provider when the
  [fan-out](../reference/glossary.md#fan-out) drains is re-run once more.

That rescue covers provider-side failures only. A second attempt would buy the
same answer at full price, so it leaves these alone:

- unparseable output;
- a blown `max_tokens` ceiling;
- a batch the oversized-diff split already retried;
- an unrecoverable failure such as a spent quota;
- any limit you set (`max_review_seconds`, `max_review_tokens`, a cancelled
  job).

Usually you will see none of this. A review that could not recover says so in
its summary and names the lens it lost.

If you are seeing rate limits, check the review's own burst before blaming the
model. Every `(batch, lens)` call shares one concurrency pool and one API key.
So a wide fan-out can throttle itself:

```yaml
max_concurrency: 3
```

Beyond that, it is a question about your OpenRouter account. Limits on free
model variants (the `:free` suffix) are account-wide rather than per-model. The
daily allowance depends on how much credit the account has bought. So a review
is far more likely to be throttled on a free variant than a paid one.

Paid models carry no OpenRouter platform request cap, though the upstream
provider behind a model can still rate-limit you. Neither is the same as a
per-key credit spending cap. That cap limits what a key may spend, not how often
it may call.

## Persist Non-Secret Defaults

Provider and model are not secrets, so they can live in `.lgtmaybe.yml`. The key
stays in the environment.

```yaml
provider: openrouter
model: anthropic/claude-sonnet-4.6
```

With that file in place, `lgtmaybe review` needs no flags. See
[Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) for every setting.

[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-openrouter.yml
