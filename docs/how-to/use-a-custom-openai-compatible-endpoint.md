---
description: Point lgtmaybe at any OpenAI-compatible server - vLLM, llama.cpp, LM Studio, DeepSeek - with --api-base; the key is optional.
---

# Local Models and Other OpenAI-Compatible Providers

Many model servers speak the OpenAI `/v1` wire format: local and self-hosted
runtimes like [vLLM][vllm], [llama.cpp][llamacpp] and [LM Studio][lmstudio],
plus hosted APIs like [DeepSeek][deepseek] and many proxies. The
`openai-compatible` provider points lgtmaybe at any of them: you supply the base
URL and, if the server wants one, a key.

Use this provider when the model you want is not in the built-in provider list.

> Some endpoints that could run through here have a first-class provider
> instead, which needs less setup: [z.ai / GLM](review-with-zai.md) (`zai`) and
> [ollama](run-locally-with-ollama.md) (`ollama`).

## Contents

- [Local models at a glance](#local-models-at-a-glance)
- [How it works](#how-it-works)
- [DeepSeek (hosted, keyed)](#deepseek-hosted-keyed)
- [llama.cpp (local, keyless)](#llamacpp-local-keyless)
- [LM Studio (local, keyless)](#lm-studio-local-keyless)
- [vLLM (local or self-hosted, keyless)](#vllm-local-or-self-hosted-keyless)
- [Concurrency: what each server can actually take](#concurrency-what-each-server-can-actually-take)
- [Persist it in `.lgtmaybe.yml`](#persist-it-in-lgtmaybeyml)
- [Gateways that don't support JSON mode (`response_format`)](#gateways-that-dont-support-json-mode-response_format)

## Local Models at a Glance

Run a model on your own hardware, with no cost, no key, and nothing leaving the
machine:

| Runtime | Provider | Key needed? | See |
|---|---|---|---|
| [ollama][ollama] | `ollama` (native) | No | [Run locally with ollama](run-locally-with-ollama.md) |
| [vLLM][vllm] | `openai-compatible` | No | [below](#vllm-local-or-self-hosted-keyless) |
| [llama.cpp][llamacpp] | `openai-compatible` | No | [below](#llamacpp-local-keyless) |
| [LM Studio][lmstudio] | `openai-compatible` | No | [below](#lm-studio-local-keyless) |

ollama has its own `--provider ollama` and [its own guide](run-locally-with-ollama.md).
vLLM, llama.cpp and LM Studio are reached through `openai-compatible` and their
local server's `--api-base`, as shown below.

**Which model, and will it fit?** The ollama guide's model-choice and hardware
advice applies to any local runtime: pick a coding model, prefer bigger and
newer, and size it to your RAM/VRAM. See
[Which model, and will it fit?](run-locally-with-ollama.md#which-model-and-will-it-fit).

## How It Works

`--provider openai-compatible` routes through litellm's OpenAI client but sends
requests to the `--api-base` you give instead of `api.openai.com`. The base URL
is required (`--api-base` or `OPENAI_COMPATIBLE_API_BASE`); the API key is
optional:

- **Hosted endpoints** (DeepSeek, a paid proxy) need a key. Pass `--api-key` or
  set `OPENAI_COMPATIBLE_API_KEY`.
- **Local servers** (llama.cpp, LM Studio, vLLM) usually need none. lgtmaybe
  then sends a harmless placeholder key, because the OpenAI client rejects an
  empty one.

A key you supply is read from the environment or `--api-key` and is never
persisted to config.

Because the endpoint might be a slow local model, `openai-compatible` gets the
same generous 1800-second per-call timeout as ollama (scaled for queueing, see
[Concurrency](#concurrency-what-each-server-can-actually-take)), and the same
default output ceiling of 16384 tokens per call. For a fast hosted endpoint like
DeepSeek you can lower the timeout with `--timeout` (or `timeout:` in config).

## DeepSeek (Hosted, Keyed)

```bash
export OPENAI_COMPATIBLE_API_KEY=sk-...        # your DeepSeek key

lgtmaybe review \
  --provider openai-compatible \
  --model deepseek-chat \
  --api-base https://api.deepseek.com/v1
```

You can pass the key with `--api-key sk-...` instead of the env var.

## llama.cpp (Local, Keyless)

Start the server:

```bash
llama-server -m ./model.gguf --port 8000        # serves the OpenAI API at /v1
```

Then review against it, with no key:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model local-model \
  --api-base http://localhost:8000/v1
```

## LM Studio (Local, Keyless)

Enable the local server in LM Studio (it serves the OpenAI API on port `1234` by
default), then:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model your-loaded-model \
  --api-base http://localhost:1234/v1
```

## vLLM (Local or Self-Hosted, Keyless)

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --api-base http://localhost:8000/v1
```

## Concurrency: What Each Server Can Actually Take

lgtmaybe fans out across a pool sized by `max_concurrency`, 6 by default on
every provider. That is a ceiling on what it will have in flight; whether your
server runs the calls together is up to the server, and the three above differ
sharply:

| Server | Concurrent by default? | Raising it |
|---|---|---|
| **vLLM** | Yes, continuous batching | Close to free; each request keeps the full `--max-model-len` |
| **llama.cpp** | No, one slot | `-np N` adds slots, but **splits one KV cache between them** |
| **LM Studio** | No, single slot | Not exposed; set `max_concurrency` to 1 |

The llama.cpp trap fails as a quality problem rather than an error. `-np`
divides the context you asked for: `-c 32768 -np 4` leaves each slot 8k, and a
lgtmaybe review prompt is comfortably larger than that, so the diff is silently
truncated and the model reviews something it was never fully shown. Size `-c` as
`slots × per-slot context`:

```bash
llama-server -m ./model.gguf --port 8000 -np 4 -c 131072   # 4 slots × 32k each
```

vLLM allocates KV blocks dynamically rather than up front, so concurrent
requests each keep the full `--max-model-len`. That makes it the local server to
choose when review latency matters:

```bash
vllm serve <model> --port 8000 --max-model-len 32768
lgtmaybe review --provider openai-compatible --model <model> \
  --api-base http://localhost:8000/v1 --max-concurrency 6
```

**Queueing does not cost you a timeout.** A queued request's clock starts when it
is sent, not when a slot frees, so lgtmaybe scales the `openai-compatible`
default timeout (1800 seconds) by the fan-out width: `1800 × 6` at the default,
bounded by `max_review_seconds` (3600 seconds; `0` disables the deadline and the
bound with it). It is never trimmed below the 1800-second provider default, so a
deadline set lower than that does not shrink it. The bound applies to the budget,
not the wall clock: the deadline gates when a call may start, and does not cut a
running call short. An explicit `timeout` is used exactly as written, at any
width. Each run logs the number it resolved and the width it assumed:

```
per-call budget resolved  timeout_s=3600  timeout_source="provider default"  concurrency=6
```

For a single-slot server, you can say so and have lgtmaybe queue nothing:

```yaml
# .lgtmaybe.yml
max_concurrency: 1
```

## Persist It in `.lgtmaybe.yml`

The provider, model and base URL are not secrets, so they can live in config.
The key stays in the environment.

```yaml
provider: openai-compatible
model: deepseek-chat
api_base: https://api.deepseek.com/v1
```

With that file in place, `lgtmaybe review` needs no flags. In the GitHub Action,
set the same values as inputs (or in `.lgtmaybe.yml`) and pass `api_key` from a
secret for hosted endpoints; see
[`examples/workflows/review-openai-compatible.yml`][wf]. Leave `api_key` empty for
a keyless server on the runner host, reached from the Action's container at
`http://host.docker.internal:<port>/v1`.

## Gateways That Don't Support JSON Mode (`response_format`)

To keep models returning clean findings instead of prose, lgtmaybe asks for
structured output through the OpenAI `response_format` parameter (JSON mode).
Most endpoints honour it. Some enterprise gateways and custom proxies don't:
they either ignore it, so the model answers with the JSON in a ```` ```json ````
fence or surrounded by prose, or reject the request with a `400 Bad Request`.

lgtmaybe handles the first case for you: the parser strips fences and pulls the
JSON out of surrounding prose, so a gateway that ignores `response_format` still
produces a normal review.

A third case is common with LM Studio serving a "thinking" model (e.g.
qwen3.x): the server accepts `response_format`, but the schema-constrained
decoder returns empty content. lgtmaybe handles this too. It retries an empty
reply once with the schema sent as a forced tool call instead; if the model
answers empty under that as well, lgtmaybe stops sending a schema to that model
and the parser reads the findings from plain text. No flag needed.

When a gateway rejects `response_format` with a `400`, lgtmaybe recognises some
refusals that name the parameter and steps down by itself. If yours still fails,
turn structured output off so the request never carries the parameter. The
prompt still asks for JSON and the lenient parser still does its job:

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
