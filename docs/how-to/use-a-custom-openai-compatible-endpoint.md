# Use a Custom OpenAI-Compatible Endpoint

Lots of model servers speak the OpenAI `/v1` wire format: [DeepSeek's API][deepseek],
[llama.cpp][llamacpp]'s server, [LM Studio][lmstudio], [vLLM][vllm], and many
hosted proxies. The `openai-compatible` provider points lgtmaybe at any of them —
you supply the base URL, and (if the server wants one) a key.

This is the answer to "I don't want to be limited to the built-in provider list":
anything that exposes an OpenAI-compatible `/v1` endpoint works through one flag.

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
the same generous **300s** per-call timeout as ollama. For a fast hosted endpoint
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
endpoints honour it. Some enterprise gateways and custom proxies don't — they
either **ignore** it (the model then answers with the JSON wrapped in a
```` ```json ```` fence or surrounded by conversational prose) or **reject** the
request outright with a `400 Bad Request`.

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
