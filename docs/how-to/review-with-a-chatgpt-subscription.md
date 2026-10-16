---
description: Review your local changes with a ChatGPT Plus or Pro subscription instead of an OpenAI API key.
---

# Review with a ChatGPT Subscription

The `chatgpt` [provider](../reference/glossary.md#provider) reviews with your
ChatGPT Plus or Pro subscription. You sign in once with a code in your browser.
There is no API key to create or pay for.

It is for reviews on your own machine: `lgtmaybe review`, `lgtmaybe diagram`
and `lgtmaybe mcp`. For CI, use the [OpenAI provider](review-with-openai.md)
with an API key.

## Contents

- [Sign In](#sign-in)
- [Run a Review](#run-a-review)
- [Use It from a Coding Agent](#use-it-from-a-coding-agent)
- [Choosing the Model](#choosing-the-model)
- [What Differs from the OpenAI Provider](#what-differs-from-the-openai-provider)
- [Troubleshooting](#troubleshooting)

## Sign In

```bash
lgtmaybe login chatgpt
```

lgtmaybe prints a URL and a one-time code. Open the URL, sign in to ChatGPT and
enter the code. The command waits until you approve it, for up to 15 minutes.
It then prints where the token is stored.

The sign-in is the same one the Codex CLI uses. lgtmaybe signs in through
[litellm](https://docs.litellm.ai/), which keeps the token at
`~/.config/litellm/chatgpt/auth.json` and refreshes it for you. You only sign
in again if the token is revoked or stops refreshing.

Never share the code. Device codes are a common phishing target.

## Run a Review

```bash
lgtmaybe review --provider chatgpt --model gpt-5.5
```

Provider and model are not secrets, so they can live in `.lgtmaybe.yml`:

```yaml
provider: chatgpt
model: gpt-5.5
```

The token stays in litellm's file. lgtmaybe never copies it into its own
config.

## Use It from a Coding Agent

The [MCP](../reference/glossary.md#mcp) server takes the same flags:

```bash
claude mcp add lgtmaybe -- lgtmaybe mcp --provider chatgpt --model gpt-5.5
```

Sign in with `lgtmaybe login chatgpt` before you start the agent. The server
never asks you to sign in itself. See
[Use from a Coding Agent over MCP](use-from-a-coding-agent-over-mcp.md).

## Choosing the Model

Pass a model your plan can use in Codex, such as `gpt-5.5` or
`gpt-5.3-codex`. Use the name without a prefix. lgtmaybe adds the `chatgpt/`
route for you.

Reviews count against your plan's Codex usage limits. A review sends one
request per [lens](../reference/glossary.md#lens), several at once. On a large
change, a Plus plan can hit its limit part-way through. If it does, set
`max_concurrency: 2` in `.lgtmaybe.yml` or review a smaller change.

## What Differs from the OpenAI Provider

The subscription reaches OpenAI's Codex backend, not the public API. That
backend accepts fewer request settings:

- **The review format is sent as a tool call.** lgtmaybe still holds the model
  to its JSON schema. Nothing changes in what gets posted.
- **`temperature` is ignored.** The default of `0.0` cannot reach the model.
  So two runs on the same change can differ, and the run log says so.
- **`max_tokens` is ignored.** The backend applies its own output limit.

The backend is not a published API. OpenAI can change it without notice, and
an lgtmaybe update may be needed when it does.

## Troubleshooting

**"no sign-in was found"**: run `lgtmaybe login chatgpt`. The message names the
file it looked for. If you set `CHATGPT_TOKEN_DIR`, check that it points at
the same directory you signed in with.

**"the ChatGPT sign-in is missing or has expired"**: the stored token could not
be refreshed. Run `lgtmaybe login chatgpt` again.

**A review in CI fails with "no sign-in was found"**: this is expected. A CI
runner has no stored sign-in. Use `--provider openai` with an
`OPENAI_API_KEY` secret there.
