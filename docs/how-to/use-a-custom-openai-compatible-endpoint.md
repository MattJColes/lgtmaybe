---
description: Point lgtmaybe at any OpenAI-compatible server - vLLM, llama.cpp, LM Studio, DeepSeek - with --api-base; the key is optional.
---

# Other OpenAI-Compatible Servers

Many model servers speak the OpenAI `/v1` wire format (the request and reply
shape of OpenAI's API). They include local and self-hosted runtimes like
[vLLM][vllm], [llama.cpp][llamacpp] and [LM Studio][lmstudio]. They also include
hosted APIs like [DeepSeek][deepseek] and many proxies. The `openai-compatible`
provider points lgtmaybe at any of them. You supply the base URL and, if the
server wants one, a key.

Use this provider when the model you want is not in the built-in provider list.

> **Running a model on your own machine?** Start with
> [Run locally](run-locally.md). It covers ollama, llama.cpp and vLLM end to
> end: model choice, hardware, context windows and concurrency. This page
> covers the provider itself, hosted endpoints and LM Studio.
>
> [z.ai / GLM](review-with-zai.md) could also run through here, but it has a
> first-class provider (`zai`) that needs less setup.

## Contents

- [Local models at a glance](#local-models-at-a-glance)
- [How it works](#how-it-works)
- [DeepSeek (hosted, keyed)](#deepseek-hosted-keyed)
- [LM Studio (local, keyless)](#lm-studio-local-keyless)
- [Persist it in `.lgtmaybe.yml`](#persist-it-in-lgtmaybeyml)
- [Gateways that don't support JSON mode (`response_format`)](#gateways-that-dont-support-json-mode-response_format)

## Local Models at a Glance

Run a model on your own hardware, with no cost, no key, and nothing leaving the
machine:

| Runtime | Provider | Key needed? | See |
|---|---|---|---|
| [ollama][ollama] | `ollama` (native) | No | [Run locally](run-locally.md#ollama) |
| [llama.cpp][llamacpp] | `openai-compatible` | No | [Run locally](run-locally.md#llamacpp) |
| [vLLM][vllm] | `openai-compatible` | No | [Run locally](run-locally.md#vllm) |
| [LM Studio][lmstudio] | `openai-compatible` | No | [below](#lm-studio-local-keyless) |

For model choice and hardware on any of them, see
[Which model, and will it fit?](run-locally.md#which-model-and-will-it-fit).

## How It Works

`--provider openai-compatible` routes through litellm's OpenAI client. It sends
requests to the `--api-base` you give instead of `api.openai.com`. The base URL
is required (`--api-base` or `OPENAI_COMPATIBLE_API_BASE`). The API key is
optional:

- **Hosted endpoints** (DeepSeek, a paid proxy) need a key. Pass `--api-key` or
  set `OPENAI_COMPATIBLE_API_KEY`.
- **Local servers** (llama.cpp, LM Studio, vLLM) usually need none. lgtmaybe
  then sends a harmless placeholder key, because the OpenAI client rejects an
  empty one.

A key you supply is read from the environment or `--api-key` and is never
persisted to config.

The endpoint might be a slow local model. So `openai-compatible` gets the same
defaults as ollama:

- the same generous 1800-second per-call timeout (scaled for queueing, see
  [Slow models and timeouts](run-locally.md#slow-models-and-timeouts));
- the same default output ceiling of 16384 tokens per call.

For a fast hosted endpoint like DeepSeek, you can lower the timeout with
`--timeout` (or `timeout:` in config).

## DeepSeek (Hosted, Keyed)

```bash
export OPENAI_COMPATIBLE_API_KEY=sk-...        # your DeepSeek key

lgtmaybe review \
  --provider openai-compatible \
  --model deepseek-chat \
  --api-base https://api.deepseek.com/v1
```

You can pass the key with `--api-key sk-...` instead of the env var.

## LM Studio (Local, Keyless)

Enable the local server in LM Studio (it serves the OpenAI API on port `1234` by
default), then:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model your-loaded-model \
  --api-base http://localhost:1234/v1
```

LM Studio serves one request at a time and does not expose a setting to raise
it. lgtmaybe's other calls queue rather than fail, and the default timeout
already allows for the wait. To skip the queue, set `max_concurrency: 1`. See
[Concurrency on a local server](run-locally.md#concurrency-on-a-local-server).

## Persist It in `.lgtmaybe.yml`

The provider, model and base URL are not secrets, so they can live in config.
The key stays in the environment.

```yaml
provider: openai-compatible
model: deepseek-chat
api_base: https://api.deepseek.com/v1
```

With that file in place, `lgtmaybe review` needs no flags.

In the GitHub Action, set the same values as inputs (or in `.lgtmaybe.yml`). For
hosted endpoints, pass `api_key` from a secret. See
[`examples/workflows/review-openai-compatible.yml`][wf]. For a keyless server on
the runner host, see
[Inside the GitHub Action's container](run-locally.md#inside-the-github-actions-container).

## Gateways That Don't Support JSON Mode (`response_format`)

lgtmaybe wants models to return clean findings instead of prose. So it asks
for structured output through the OpenAI `response_format` parameter (JSON
mode). Most endpoints honour it. Some enterprise gateways and custom proxies
don't. They do one of two things:

- ignore it, so the model answers with the JSON in a ```` ```json ```` fence or
  surrounded by prose;
- or reject the request with a `400 Bad Request`.

lgtmaybe handles the first case for you. The parser strips fences and pulls
the JSON out of surrounding prose. So a gateway that ignores `response_format`
still produces a normal review.

A third case is common with LM Studio serving a "thinking" model (e.g.
qwen3.x): the server accepts `response_format`, but the schema-constrained
decoder returns empty content. lgtmaybe handles this too:

1. It retries an empty reply once, with the schema sent as a forced tool call
   instead.
2. If the model answers empty under that as well, lgtmaybe stops sending a
   schema to that model. The parser reads the findings from plain text.

No flag needed.

When a gateway rejects `response_format` with a `400`, lgtmaybe recognises
some refusals that name the parameter. It then steps down by itself. If yours
still fails, turn structured output off so the request never carries the
parameter. The prompt still asks for JSON and the lenient parser still does its
job:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model gemini-3.5-flash \
  --api-base https://api.myllm.com/v1 \
  --no-structured-output
```

Persist it as `structured_output: false` in `.lgtmaybe.yml`, or set the
Action's `structured_output` input to `false`.

[deepseek]: https://api-docs.deepseek.com/
[llamacpp]: https://github.com/ggml-org/llama.cpp
[lmstudio]: https://lmstudio.ai/
[vllm]: https://docs.vllm.ai/
[ollama]: https://ollama.com/
[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-openai-compatible.yml
