---
description: Point lgtmaybe at any OpenAI-compatible server — vLLM, llama.cpp, LM Studio, DeepSeek — with --api-base; the key is optional.
---

# Local Models & other OpenAI providers

Lots of model servers speak the OpenAI `/v1` wire format: local and self-hosted
runtimes like [vLLM][vllm], [llama.cpp][llamacpp], and [LM Studio][lmstudio], plus
hosted APIs like [DeepSeek][deepseek] and many proxies. The `openai-compatible`
provider points lgtmaybe at any of them — you supply the base URL, and (if the
server wants one) a key.

This is the answer to "I don't want to be limited to the built-in provider list":
anything that exposes an OpenAI-compatible `/v1` endpoint works through one flag.

> Some endpoints that *could* run through here have a first-class provider
> instead — use it for less setup: [z.ai / GLM](review-with-zai.md) (`zai`) and
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

## Local models at a glance

Run a model on your own hardware — zero cost, no key, nothing leaves the machine:

| Runtime | Provider | Key needed? | See |
|---|---|---|---|
| [ollama][ollama] | `ollama` (native) | No | [Run locally with ollama](run-locally-with-ollama.md) |
| [vLLM][vllm] | `openai-compatible` | No | [below](#vllm-local-or-self-hosted-keyless) |
| [llama.cpp][llamacpp] | `openai-compatible` | No | [below](#llamacpp-local-keyless) |
| [LM Studio][lmstudio] | `openai-compatible` | No | [below](#lm-studio-local-keyless) |

ollama has its own first-class `--provider ollama` (it's the easiest local
start), so it gets its [own guide](run-locally-with-ollama.md). vLLM, llama.cpp,
and LM Studio are reached through `openai-compatible` and the `--api-base` of
their local server, as shown below.

**Which model, and will it fit?** The same model-choice and hardware guidance
applies to any local runtime — pick a coding model, bigger and newer is more
accurate, and size it to your RAM/VRAM. See
[Which model, and will it fit?](run-locally-with-ollama.md#which-model-and-will-it-fit)
in the ollama guide.

## How it works

`--provider openai-compatible` routes through litellm's OpenAI client, but sends
your requests to the `--api-base` you give instead of `api.openai.com`. The
**base URL is required** (that's the whole point); the **API key is optional**:

- **Hosted endpoints** (DeepSeek, a paid proxy) need a key — pass `--api-key` or
  set `OPENAI_COMPATIBLE_API_KEY`.
- **Local servers** (llama.cpp, LM Studio, vLLM) usually need none. lgtmaybe
  sends a harmless placeholder key in that case, because the OpenAI client rejects
  an empty one.

The API key, when you do supply one, is read from the environment or `--api-key`
and is **never persisted** to config.

Because the endpoint might be a slow local model, `openai-compatible` defaults to
the same generous **1800s** per-call timeout as ollama. For a fast hosted endpoint
like DeepSeek you can dial it down with `--timeout` (or `timeout:` in config).

## DeepSeek (hosted, keyed)

```bash
export OPENAI_COMPATIBLE_API_KEY=sk-...        # your DeepSeek key

lgtmaybe review \
  --provider openai-compatible \
  --model deepseek-chat \
  --api-base https://api.deepseek.com/v1
```

You can pass the key inline with `--api-key sk-...` instead of the env var.

## llama.cpp (local, keyless)

Start the server:

```bash
llama-server -m ./model.gguf --port 8000        # serves the OpenAI API at /v1
```

Then review against it — no key needed:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model local-model \
  --api-base http://localhost:8000/v1
```

## LM Studio (local, keyless)

Enable the local server in LM Studio (it serves the OpenAI API, default port
`1234`), then:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model your-loaded-model \
  --api-base http://localhost:1234/v1
```

## vLLM (local or self-hosted, keyless)

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --api-base http://localhost:8000/v1
```

## Concurrency: what each server can actually take

lgtmaybe fans out across a pool sized by `max_concurrency`, **6 by default on
every provider**. That is a ceiling on what it will have in flight; whether your
server runs them together is the server's business, and the three above differ
sharply.

| server | concurrent by default? | raising it |
|---|---|---|
| **vLLM** | yes — continuous batching | close to free; each request keeps the full `--max-model-len` |
| **llama.cpp** | no, one slot | `-np N` adds slots, but **splits one KV cache between them** |
| **LM Studio** | no, single-slot | not exposed; leave `max_concurrency` at 1 |

The llama.cpp trap is worth spelling out, because it fails as a quality problem
rather than an error. `-np` divides the context you asked for: `-c 32768 -np 4`
leaves each slot **8k**, and a lgtmaybe review prompt is comfortably larger than
that, so the diff is silently truncated and the model reviews something it was
never fully shown. Size `-c` as `slots × per-slot-context`:

```bash
llama-server -m ./model.gguf --port 8000 -np 4 -c 131072   # 4 slots × 32k each
```

vLLM allocates KV blocks dynamically rather than carving them up front, so
concurrent requests each keep the full `--max-model-len`. That is why it is the
local server to reach for when review latency matters:

```bash
vllm serve <model> --port 8000 --max-model-len 32768
lgtmaybe review --provider openai-compatible --model <model> \
  --api-base http://localhost:8000/v1 --max-concurrency 6
```

**Queueing does not cost you a timeout.** A queued request's clock starts when it
is sent, not when the slot frees, so lgtmaybe scales the `openai-compatible`
per-call default (1800 s) by the fan-out width — `1800 × 6` at the default,
bounded by `max_review_seconds` (3600 s) so no call is given more time than the
whole review is allowed — a bound on the budget, not on the wall clock, since the
deadline gates when a call may start rather than cutting a running one short. An
explicit `timeout` is honoured exactly as written, at any width. Each run logs the
number it resolved and the width it assumed:

```
per-call timeout resolved  timeout_s=3600  timeout_source="provider default"  concurrency=6
```

For a single-slot server, you can still say so and let lgtmaybe queue nothing:

```yaml
# .lgtmaybe.yml
max_concurrency: 1
```

## Persist it in `.lgtmaybe.yml`

The provider, model, and base URL are non-secret defaults, so they can live in
config (the key stays in the environment):

```yaml
provider: openai-compatible
model: deepseek-chat
api_base: https://api.deepseek.com/v1
```

With that file in place, `lgtmaybe review` needs no flags. In the GitHub Action,
set the same values as inputs (or in `.lgtmaybe.yml`) and pass `api_key` from a
secret for hosted endpoints; leave it empty for keyless local servers reached at
`http://host.docker.internal:<port>/v1`.

## Gateways that don't support JSON mode (`response_format`)

To keep models returning clean findings instead of prose, lgtmaybe asks for
structured output via the OpenAI `response_format` parameter (JSON mode). Most
endpoints honour it. Some enterprise gateways and custom proxies don't. They
either **ignore** it — the model then answers with the JSON wrapped in a
```` ```json ```` fence or surrounded by conversational prose — or **reject**
the request outright with a `400 Bad Request`.

lgtmaybe handles the first case for you: the parser strips fences and pulls the
JSON out of surrounding prose, so a gateway that merely ignores `response_format`
still produces a normal review. (Older versions could fail here with
`unparseable model output` on every lens — that's fixed.)

There is a third case, common with **LM Studio fronting a "thinking" model**
(e.g. qwen3.x): the server *accepts* `response_format` but the schema-constrained
decoder returns **empty content** — every lens would otherwise fail with
`unparseable model output`. lgtmaybe handles this for you too: when a structured
call comes back empty, it drops the schema and retries once, and the model then
emits the findings as normal (fenced) text the parser reads. No flag needed.

If your gateway **rejects** `response_format` with a `400`, turn it off so the
request never carries the parameter — the prompt still asks for JSON and the
lenient parser still does its job:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model gemini-3.5-flash \
  --api-base https://api.myllm.com/v1 \
  --no-structured-output
```

Persist it as `structured_output: false` in `.lgtmaybe.yml`, or set the
`structured_output` input to `false` in the GitHub Action.

[deepseek]: https://api-docs.deepseek.com/
[llamacpp]: https://github.com/ggml-org/llama.cpp
[lmstudio]: https://lmstudio.ai/
[vllm]: https://docs.vllm.ai/
[ollama]: https://ollama.com/
