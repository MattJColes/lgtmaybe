---
description: Run lgtmaybe entirely on your own hardware with ollama, llama.cpp or vLLM - zero API cost, zero egress, no keys, and code never leaves your machine.
---

# Run Locally

Use this guide to review your local changes with a model running on your own
hardware. There is no API cost, no egress (no data goes to a hosted provider)
and no key. The CLI reviews your `git` diff and prints the findings. To post
reviews on pull requests, use the [GitHub Action](use-as-github-action.md).

Three local runtimes are covered here, and lgtmaybe works well with all of
them: [ollama][ollama], [llama.cpp][llamacpp] and [vLLM][vllm]. ollama has its
own `--provider ollama`. llama.cpp and vLLM serve the OpenAI `/v1` API, so they
use `--provider openai-compatible` with the server's URL. Everything else on
this page applies to all three unless it says otherwise.

> LM Studio also works through `openai-compatible`. It has a quirk with thinking
> models, covered in
> [Other OpenAI-compatible servers](use-a-custom-openai-compatible-endpoint.md#lm-studio-local-keyless).

## Contents

- [Pick a runtime](#pick-a-runtime)
- [Prerequisites](#prerequisites)
- [Which model, and will it fit?](#which-model-and-will-it-fit)
- [Start the server and run the review](#start-the-server-and-run-the-review)
- [Match the batch size to the server's window](#match-the-batch-size-to-the-servers-window)
- [Persist it in `.lgtmaybe.yml`](#persist-it-in-lgtmaybeyml)
- [Reviewing large files (recursive walk)](#reviewing-large-files-recursive-walk)
- [Use a remote server](#use-a-remote-server)
- [Inside the GitHub Action's container](#inside-the-github-actions-container)
- [Get findings as JSON](#get-findings-as-json)
- [Let an AI agent apply the fixes](#let-an-ai-agent-apply-the-fixes)
- [Concurrency on a local server](#concurrency-on-a-local-server)
- [The output ceiling (why a review can't run forever)](#the-output-ceiling-why-a-review-cant-run-forever)
- [Slow models and timeouts](#slow-models-and-timeouts)
- [Troubleshooting](#troubleshooting)

## Pick a Runtime

| Runtime | `--provider` | Default URL | Choose it when |
|---|---|---|---|
| [ollama][ollama] | `ollama` | `http://localhost:11434` | You want the quickest start: one command pulls a model and serves it. |
| [llama.cpp][llamacpp] | `openai-compatible` | `http://localhost:8080/v1` | You want one small binary, GGUF files and fine control over quantisation, on CPU, Apple Silicon or a GPU. |
| [vLLM][vllm] | `openai-compatible` | `http://localhost:8000/v1` | You have an NVIDIA GPU and want speed. It runs the review's parallel calls together. |

None of them needs a key on a local machine. With `openai-compatible` and no
key, lgtmaybe sends a harmless placeholder, because the OpenAI client rejects an
empty one.

## Prerequisites

- lgtmaybe installed (`pip install lgtmaybe`)
- One of the runtimes above, installed
- A local git repository with changes to review

## Which Model, and Will It Fit?

Two rules:

1. **Pick a coding model.** Reviewing a PR is a coding task. Use a model built
   for code (e.g. the Qwen3 coder line), not a general chat model.
2. **Bigger and newer is more accurate.** Use the largest, most recent coding
   model your hardware can run. Our accuracy numbers are for a small model. We
   benchmarked `qwen3.5:4b`, and it did well, but only with recursive review on
   (88% vs 61% recall). A larger, newer model catches more and relies on that
   less.

A solid mid-2026 default is Qwen3.6-27B. On coding benchmarks it is close to
frontier API models (SWE-bench Verified ~77%). Yet it is small enough for a
workstation or a well-specced laptop. So it clears lgtmaybe's bar across all the
review [lenses](../reference/glossary.md#lens) without a data-centre GPU. Each
runtime names it differently: `qwen3.6:27b` on ollama, a GGUF file on llama.cpp
and `Qwen/Qwen3.6-27B` (or its FP8 build) on vLLM.

Smaller models work too, but accuracy falls off. You will miss subtler
findings. You may also need `--no-reflect`, because the
[reflection](../reference/glossary.md#reflection) pass over-prunes on a weak
model.

**Hardware, quantised (the usual way to run it locally):**

| You have | What to expect |
|---|---|
| **< 32 GB** RAM/VRAM | Drop to a smaller model (e.g. `gemma4:e4b` on ollama) or use a hosted provider. 27B at a usable quant won't leave room for the diff. |
| **32 GB** RAM/VRAM | The practical floor. Run Qwen3.6-27B at a 4-bit quant (≈16-18 GB of weights) with a modest context window of about 32k. |
| **48 GB+** RAM/VRAM (preferred) | Comfortable. Room for the weights plus a 32k window, or 64k for very large diffs, with headroom for the KV cache. |

This applies to discrete VRAM and Apple Silicon unified memory alike. A bigger
context window costs memory on top of the weights. Each runtime sets the window
its own way: `--num-ctx` on lgtmaybe for ollama, `-c` on `llama-server` and
`--max-model-len` on `vllm serve`. The vLLM server is GPU-first. It usually runs
an FP8 or AWQ build rather than a 4-bit GGUF, so check the model card for its
memory needs. On a hosted provider none of this matters: the model runs on the
provider's hardware.

## Start the Server and Run the Review

Run `lgtmaybe review` from inside the repository, on the branch you want
reviewed. It diffs your current branch against the remote primary branch and
prints the findings. To change what it reviews:

- `--working` reviews the whole worktree (branch commits plus uncommitted edits)
  against that same base.
- `--uncommitted` reviews only your uncommitted edits against HEAD.
- `--base <ref>` diffs against a different base.

### ollama

```bash
ollama pull qwen3.6:27b        # strong all-round coding model
ollama pull gemma4:e4b         # smaller, for devices with limited RAM

lgtmaybe review --provider ollama --model qwen3.6:27b
```

`http://localhost:11434` is the default, so `--api-base` is optional. lgtmaybe
tells ollama the context window to use (`--num-ctx`, 32768 by default). Run
`ollama list` to see the models you have.

### llama.cpp

Start `llama-server` with a 32k context window. `-hf` downloads the GGUF from
Hugging Face, or use `-m ./model.gguf` for a file you already have. `--alias`
sets the model name the server answers to:

```bash
llama-server -hf unsloth/Qwen3.6-27B-GGUF:Q4_K_M \
  --alias qwen3.6-27b -c 32768 --port 8080
```

Then review against it, with no key:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model qwen3.6-27b \
  --api-base http://localhost:8080/v1 \
  --max-input-tokens 16000
```

### vLLM

Start the server with a 32k context window. The model name is the Hugging Face
id unless you set `--served-model-name`:

```bash
vllm serve Qwen/Qwen3.6-27B-FP8 --max-model-len 32768 --port 8000
```

Then review against it, with no key:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model Qwen/Qwen3.6-27B-FP8 \
  --api-base http://localhost:8000/v1 \
  --max-input-tokens 16000
```

## Match the Batch Size to the Server's Window

`--max-input-tokens` is the most tokens lgtmaybe puts in one model call. A
bigger diff is split into several calls. Its default (100000) is lowered
automatically when lgtmaybe knows the model's window:

- **ollama:** lgtmaybe sets the window itself (`--num-ctx`). So it fits the
  batch to it: `num_ctx` minus the output ceiling, 16384 tokens at the defaults.
  You don't need to set anything.
- **llama.cpp and vLLM:** the window is set on the server (`-c` or
  `--max-model-len`), and lgtmaybe cannot read it. So the 100000 default stays.
  Set `--max-input-tokens` yourself, as the commands above do.

Leave room for the answer. The server's window must hold the batch plus the
output ceiling (16384 tokens by default, see
[below](#the-output-ceiling-why-a-review-cant-run-forever)). lgtmaybe also counts
tokens with a generic tokeniser, and local models count a little differently.
So for a 32k window, `--max-input-tokens 16000` is a safe value. For a 64k
window, use about 45000.

If you forget, a small diff still works, because it fits anyway. A large diff
sends a batch bigger than the window, and the server refuses it or cuts it
short.

## Persist It in `.lgtmaybe.yml`

The provider, model and URL are not secrets, so they can live in config. Then
`lgtmaybe review` needs no flags. For llama.cpp:

```yaml
provider: openai-compatible
model: qwen3.6-27b
api_base: http://localhost:8080/v1
max_input_tokens: 16000
```

For ollama:

```yaml
provider: ollama
model: qwen3.6:27b
```

## Reviewing Large Files (Recursive Walk)

Sometimes a single file's diff is larger than the per-call token budget
(`--max-input-tokens`). Then lgtmaybe walks it
[hunk](../reference/glossary.md#hunk) by hunk, reviewing each hunk in its own
focused call. Without this, it would send the whole file and the tail would drop
out of the model's attention. The findings are merged, and inline-comment
positions still bind to the real diff.

This recursive (RLM-style) review is on by default (`recursive: true`). Files
that fit the budget are still reviewed whole, so nothing changes for small
diffs.

It helps small local models most, because a smaller prompt is easier to review
thoroughly. In our A/B benchmark a local `qwen3.5:4b` caught all 6 planted bugs
reviewing recursively, versus 4 of 6 reviewing each file whole. The two it
missed were both in the file's tail, even though the diff fit the context
window. So the gain is focus, not only avoiding truncation.

It is a single non-deterministic run on one fixture, so treat it as
directional. The harness is described in
[DEVELOPMENT.md](https://github.com/MattJColes/lgtmaybe/blob/main/DEVELOPMENT.md#benchmarking-the-recursive-rlm-walk).

To review each file whole instead, pass `--no-recursive`. That makes one call
per file, which keeps all of a file's hunks in view together. It tends to miss
more on big files with small models.

```bash
lgtmaybe review --provider ollama --model qwen3.5:4b --no-recursive
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
recursive: false
```

## Use a Remote Server

If the server runs on another machine (e.g. a Tailscale peer), point
`--api-base` at it:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b \
  --api-base http://100.x.x.x:11434

lgtmaybe review --provider openai-compatible --model qwen3.6-27b \
  --api-base http://100.x.x.x:8080/v1 --max-input-tokens 16000
```

ollama has no authentication built in, so restrict network access at the host
or firewall. `llama-server` and `vllm serve` both take `--api-key <key>` to
require one. Pass the same key to lgtmaybe with `OPENAI_COMPATIBLE_API_KEY` (or
`--api-key`). It is read from the environment and never saved to config.

## Inside the GitHub Action's Container

The Action runs lgtmaybe in a container. So it reaches a server on the runner
host at `host.docker.internal`, not `localhost`. Set it with the Action's
`provider`, `model` and `api_base` inputs, or in `.lgtmaybe.yml`:

```yaml
provider: ollama
model: qwen3.6:27b
api_base: http://host.docker.internal:11434
```

For llama.cpp or vLLM, use `provider: openai-compatible` and
`http://host.docker.internal:<port>/v1`. Leave the `api_key` input empty for a
keyless server. See
[`examples/workflows/review-openai-compatible.yml`][wf].

## Get Findings as JSON

The CLI prints a readable listing by default and never posts anywhere. Add
`--json` for a machine-readable array you can pipe into other tools:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b --json
```

## Let an AI Agent Apply the Fixes

`--format agent` prints the findings as correction instructions. An AI coding
agent (such as Claude Code) can read and apply them. So you can review and fix a
branch locally before opening a PR. See
[Fix findings with an AI agent](fix-findings-with-an-ai-agent.md).

## Concurrency on a Local Server

lgtmaybe [fans out](../reference/glossary.md#fan-out) its review calls across a
pool sized by `max_concurrency`. It is 6 by default on every provider, local
included. That is a ceiling on what lgtmaybe will have in flight. Whether your
server runs the calls together is up to the server, and the three differ
sharply:

| Server | Concurrent by default? | Raising it |
|---|---|---|
| **ollama** | No, one request at a time | `OLLAMA_NUM_PARALLEL=N`; each slot allocates its own `num_ctx` |
| **llama.cpp** | No, one slot | `-np N` adds slots, but **splits one KV cache between them** |
| **vLLM** | Yes, continuous batching | Close to free; each request keeps the full `--max-model-len` |

Queued calls are not lost and do not fail. The wall clock is your server's
throughput either way, so raising `max_concurrency` alone changes nothing. The
setting that matters is on the server, and each one costs memory differently:

- **ollama** queues the extra calls (up to `OLLAMA_MAX_QUEUE`, 512). Start it
  with `OLLAMA_NUM_PARALLEL=4` and four calls really do run together. ollama
  allocates the context window per slot, so four slots at `num_ctx` 32768 need
  128k of context, not 32k. This is the usual reason a machine that reviewed
  happily with one slot falls over with four.
- **llama.cpp** divides the context you asked for between its slots:
  `-c 32768 -np 4` leaves each slot 8k. A lgtmaybe review prompt is comfortably
  larger than that. So the diff is silently truncated, and the model reviews
  something it was never fully shown. This fails as a quality problem rather
  than an error. Size `-c` as `slots × per-slot context`:

  ```bash
  llama-server -hf unsloth/Qwen3.6-27B-GGUF:Q4_K_M --alias qwen3.6-27b \
    --port 8080 -np 4 -c 131072      # 4 slots × 32k each
  ```

- **vLLM** allocates KV blocks (its per-request memory for the context) as it
  needs them, not up front. So concurrent requests each keep the full
  `--max-model-len`. That makes it the local server to choose when review
  latency matters.

So the two settings should match. If you have raised the server, tell lgtmaybe:

```bash
lgtmaybe review --max-concurrency 4
```

**How to tell which you got.** `--profile` answers it. Compare the per-call
`elapsed` column with the `review` stage total:

- If the calls' elapsed times sum to roughly the stage time, they ran one after
  another.
- If they sum to well over it, they overlapped.

```
review             95.32s        <- stage total
correctness         52.02s   |
spec                45.64s   |   sum is ~3x the stage,
artefacts           43.67s   |   so these overlapped
security            43.30s   |
```

**Queue time is already covered.** A queued call's timeout clock starts when the
request is sent, not when your server gets to it. So on a single-slot server,
the last call in a six-wide fan-out would otherwise have to finish within the
same budget as the first. lgtmaybe scales the local default timeout by the
fan-out width to cover that (see [below](#slow-models-and-timeouts)). You should
not need to intervene. If you would rather the calls didn't queue at all:

```yaml
# .lgtmaybe.yml
max_concurrency: 1
```

## The Output Ceiling (Why a Review Can't Run Forever)

A local model under structured output sometimes fails to stop and keeps
decoding. With no ceiling, only the per-call timeout ends it. That can mean half
an hour of sustained GPU for a single lens on a one-file diff, and hours for a
whole review.

So a review on `ollama` or `openai-compatible` runs with a default output
ceiling of 16384 tokens per call. That is half the default 32768 `num_ctx`.
Measured findings payloads are hundreds of tokens. The rest is headroom for a
thinking model, which draws its reasoning from the same budget.

The same ceiling applies to `openrouter`, because the failure is not specific to
local models. Benchmarking found:

- a model behind `openai-compatible` emitting 223,558 tokens on a single lens
  call;
- a hosted model on `openrouter` returning a 393k-token response that parsed
  into 699 false positives on a diff with nothing wrong in it.

The first-party APIs (openai, anthropic, zai, bedrock, vertex, azure) get no
default ceiling. They have not shown this failure, and a cap could truncate long
findings payloads.

The ceiling is a fixed number, not a fraction of your window. Raising the
window for a big diff adds room for the prompt, not for a longer answer. Use
`max_tokens` when you want a longer answer.

Every run logs the ceiling it resolved and where it came from:

```
per-call budget resolved  timeout_s=3600  max_tokens=16384  max_tokens_source="provider default"
```

If a lens hits it, you get a truncation notice, not a silent clean review:

```
response hit the 16384-token `max_tokens` ceiling before finishing. The batch
is re-reviewed in smaller pieces automatically, so a lens that keeps doing it is
usually generation instability in the model, which a higher ceiling makes more
expensive rather than prevents
```

**One truncation is not a reason to raise the cap.** The batch is re-reviewed in
smaller pieces on its own, so a lens that trips it occasionally has still been
reviewed. A model that runs away repeatedly is generating past the ceiling, not
being cut short by it. A bigger ceiling only makes a longer wasted call.
Changing the model is what fixes it.

Raising it is right when a genuinely long answer is being cut off: a large diff
with many real findings, where most are salvaged each time. Then raise it. Or
remove the cap with `0`, which leaves the timeout as the run's only stop:

```bash
lgtmaybe review --provider ollama --model qwen3.5:4b --max-tokens 32768
lgtmaybe review --provider ollama --model qwen3.5:4b --max-tokens 0  # uncapped
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
max_tokens: 32768
```

On llama.cpp or vLLM, a higher ceiling needs a bigger server window too, since
the window must hold the batch plus the answer.

Lowering the ceiling is the quickest way to get a result out of a slow model.
`--max-tokens 512` turns a stuck review into one that finishes in minutes and
reports what it truncated.

## Slow Models and Timeouts

Local models are slow, especially large ones on CPU. So `ollama` and
`openai-compatible` get a long default per-call timeout of 1800 seconds. You
don't need to set anything for a normal run. (Direct cloud providers default to
600 seconds.)

**That default scales with the fan-out.** Because the calls queue, the budget
has to cover the wait as well as the work:

- At the default width of six, the per-call timeout is `1800 × 6`.
- That is bounded by `max_review_seconds` (3600 seconds by default). `0`
  disables the deadline and with it the bound.
- It is never trimmed below the provider's own 1800 seconds. A deadline set
  lower than that does not shrink it.
- The bound applies to the budget, not the wall clock. The deadline decides when
  a call may start, so a call that begins just inside it still runs its full
  budget.
- At `max_concurrency: 1` there is no queue, and the budget is the plain 1800
  seconds.

Every run logs the number it resolved, and why:

```
per-call budget resolved  timeout_s=3600  timeout_source="provider default"  concurrency=6
```

An explicit `timeout` is never scaled: `timeout: 600` means 600 at any width.

If a big model still times out, you'll see
`litellm.Timeout: Connection timed out after 3600.0 seconds`. Raise the timeout
explicitly:

```bash
# CLI flag (seconds):
lgtmaybe review --provider ollama --model qwen3.6:35b --timeout 3600
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
timeout: 3600
```

The review makes five lens calls under the default `fast`
[preset](../reference/glossary.md#preset) (one per category under
`--preset full`). On a single-slot server those queue rather than overlap. So a
slow model takes roughly `lens calls × per-call time`. That is why `fast` is the
default: five calls instead of one per category is the biggest single local
speed-up.

To go faster:

- Narrow the lenses with `categories:` in `.lgtmaybe.yml` (e.g. just `security`
  and `correctness`).
- Use a smaller model.
- Give the server more GPU.
- Let the server run requests in parallel, if you have the memory, as described
  [above](#concurrency-on-a-local-server). vLLM does this out of the box. On
  ollama, `OLLAMA_NUM_PARALLEL=5` makes the same five calls overlap instead of
  queueing, close to a 5× wall-clock win.

Add `--profile` to any run to see the per-call breakdown.

## Troubleshooting

**`Connection refused`**: make sure the server is running and the `--api-base`
URL is reachable. The default ports are 11434 (ollama), 8080 (llama.cpp) and
8000 (vLLM). For llama.cpp and vLLM the URL ends in `/v1`.

**Model not found**: on ollama, run `ollama pull <model>` first. On vLLM,
`--model` must match the served name: the Hugging Face id, or
`--served-model-name` if you set it.

**`review incomplete: every review call failed`**: every lens call timed out or
returned output that wasn't valid JSON. To fix it:

- raise `--timeout`;
- try a model that follows instructions more reliably;
- or check the `LITELLM_LOG=DEBUG` output for the underlying error.

lgtmaybe reports this and exits non-zero rather than pretending the PR is clean.

For a large diff this can mean the prompt plus the findings don't fit in the
server's context window, so the output is truncated. Check the window first:

- **ollama:** lgtmaybe runs ollama with a 32768-token context (`num_ctx`) and
  structured JSON output, which covers most reviews. Raise it with `--num-ctx`
  (ollama only) so the whole diff and the findings fit.
- **llama.cpp and vLLM:** raise `-c` or `--max-model-len` on the server, and
  keep `--max-input-tokens` well under it (see
  [above](#match-the-batch-size-to-the-servers-window)).

```bash
# A large multi-file diff on ollama: more time and more context
# (32768 is already the default; go above it for very large diffs):
lgtmaybe review --provider ollama --model qwen3.6:35b \
  --timeout 1800 --num-ctx 65536
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
provider: ollama
model: qwen3.6:35b
timeout: 1800
num_ctx: 65536
```

A bigger window needs enough RAM/VRAM on the server host, so size it to your
machine.

> **If you set `--max-input-tokens` yourself on ollama, keep it under
> `--num-ctx`.** The two are independent. `--max-input-tokens` caps each batch
> lgtmaybe sends, while `--num-ctx` is the window ollama allocates. An explicit
> value is used as given, not fitted. So leave headroom: use a batch budget well
> below the window (e.g. `--max-input-tokens 16000` with `--num-ctx 32768`).
> That avoids ollama silently truncating the findings JSON, which otherwise
> shows up only as an unhelpful "review failed".

lgtmaybe does not send ollama's `think` flag. ollama already turns thinking on
for a model that supports it and rejects the flag for one that does not. So
sending nothing is right for both.

**Review is empty or truncated**: the diff may exceed the model's context
window. To shrink it, you can:

- narrow it with `include_paths` / `exclude_paths` in `.lgtmaybe.yml`;
- set a lower `max_files` in `.lgtmaybe.yml`;
- or run a model with a bigger context window.

[llamacpp]: https://github.com/ggml-org/llama.cpp
[vllm]: https://docs.vllm.ai/
[ollama]: https://ollama.com/
[wf]: https://github.com/MattJColes/lgtmaybe/blob/main/examples/workflows/review-openai-compatible.yml
