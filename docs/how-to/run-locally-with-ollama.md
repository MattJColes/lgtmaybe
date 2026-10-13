---
description: Run lgtmaybe entirely locally with an ollama model - zero API cost, zero egress, no keys, and code never leaves your machine.
---

# Run Locally with ollama

Use this guide to review your local changes with a local ollama model: no API
cost, no egress, no keys. The CLI reviews your `git` diff and prints the
findings. To post reviews on pull requests, use the
[GitHub Action](use-as-github-action.md).

> **ollama is the easiest local start.** Running a different local server
> (vLLM, llama.cpp or LM Studio) or a hosted OpenAI-compatible API like
> DeepSeek? Use the `openai-compatible` provider instead:
> [Other OpenAI-compatible servers](use-a-custom-openai-compatible-endpoint.md).
> The [model-choice and hardware guidance below](#which-model-and-will-it-fit)
> applies to any local runtime.

## Contents

- [Prerequisites](#prerequisites)
- [Pull the model you want](#pull-the-model-you-want)
- [Which model, and will it fit?](#which-model-and-will-it-fit)
- [Run the review](#run-the-review)
- [Reviewing large files (recursive walk)](#reviewing-large-files-recursive-walk)
- [Use a remote ollama instance](#use-a-remote-ollama-instance)
- [Inside the GitHub Action's container](#inside-the-github-actions-container)
- [Get findings as JSON](#get-findings-as-json)
- [Let an AI agent apply the fixes](#let-an-ai-agent-apply-the-fixes)
- [Concurrency on a local server](#concurrency-on-a-local-server)
- [The output ceiling (why a review can't run forever)](#the-output-ceiling-why-a-review-cant-run-forever)
- [Slow models and timeouts](#slow-models-and-timeouts)
- [Troubleshooting](#troubleshooting)

## Prerequisites

- lgtmaybe installed (`pip install lgtmaybe`)
- [ollama](https://ollama.com) installed and running
- A local git repository with changes to review

## Pull the Model You Want

```bash
ollama pull qwen3.6:27b        # strong all-round coding model
ollama pull gemma4:e4b         # smaller, for devices with limited RAM
```

List the models you have:

```bash
ollama list
```

## Which Model, and Will It Fit?

Two rules:

1. **Pick a coding model.** Reviewing a PR is a coding task, so use a model
   built for code (e.g. the Qwen3 coder line), not a general chat model.
2. **Bigger and newer is more accurate.** Use the largest, most recent coding
   model your hardware can run. Our accuracy numbers are for a small model: we
   benchmarked `qwen3.5:4b`, and it did well, but only with recursive review on
   (88% vs 61% recall). A larger, newer model catches more and relies on that
   less.

A solid mid-2026 default is Qwen3.6-27B (`qwen3.6:27b`). It is close to frontier
API models on coding benchmarks (SWE-bench Verified ~77%) yet small enough for a
workstation or a well-specced laptop, so it clears lgtmaybe's bar across all the
review lenses without a data-centre GPU. Smaller models work too, but accuracy
falls off: you will miss subtler findings and may need `--no-reflect`, because
the reflection pass over-prunes on a weak model.

**Hardware, quantised (the usual way to run it locally):**

| You have | What to expect |
|---|---|
| **< 32 GB** RAM/VRAM | Drop to a smaller model (`gemma4:e4b`) or use a hosted provider. 27B at a usable quant won't leave room for the diff. |
| **32 GB** RAM/VRAM | The practical floor. Run `qwen3.6:27b` at a 4-bit quant (≈16-18 GB of weights) with a modest context window. Keep `num_ctx` conservative so the model plus the diff and findings fit. |
| **48 GB+** RAM/VRAM (preferred) | Comfortable. Room for the weights plus the default `--num-ctx` (32k), or a raised 64k window for very large diffs, with headroom for the KV cache. |

This applies to discrete VRAM and Apple Silicon unified memory alike. A bigger
context window costs memory on top of the weights, so if you raise `--num-ctx`
for a large diff (see [Slow models and timeouts](#slow-models-and-timeouts)),
size it to the table above. On a hosted provider none of this matters: the model
runs on the provider's hardware.

## Run the Review

From inside the repository, on the branch you want reviewed:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

`http://localhost:11434` is the default, so `--api-base` is optional here.

This diffs your current branch against the remote primary branch and prints the
findings. Add `--working` to review the whole worktree (branch commits plus
uncommitted edits) against that same base, `--uncommitted` to review only your
uncommitted edits against HEAD, or `--base <ref>` to diff against a different
base.

## Reviewing Large Files (Recursive Walk)

When a single file's diff is larger than the per-call token budget
(`--max-input-tokens`), lgtmaybe walks it hunk by hunk, reviewing each hunk in
its own focused call, instead of sending the whole file and letting the tail drop
out of the model's attention. The findings are merged, and inline-comment
positions still bind to the real diff. This recursive (RLM-style) review is on
by default (`recursive: true`). Files that fit the budget are still reviewed
whole, so nothing changes for small diffs.

It helps small local models most, because a smaller prompt is easier to review
thoroughly. In our A/B benchmark a local `qwen3.5:4b` caught all 6 planted bugs
reviewing recursively, versus 4 of 6 reviewing each file whole. The two it
missed were both in the file's tail, even though the diff fit the context window,
so the gain is focus, not just avoiding truncation. It is a single
non-deterministic run on one fixture, so treat it as directional; the harness is
described in
[DEVELOPMENT.md](https://github.com/MattJColes/lgtmaybe/blob/main/DEVELOPMENT.md#benchmarking-the-recursive-rlm-walk).

To review each file whole instead (one call per file, which keeps all of a
file's hunks in view together but tends to miss more on big files with small
models), pass `--no-recursive`:

```bash
lgtmaybe review --provider ollama --model qwen3.5:4b \
  --api-base http://localhost:11434 --no-recursive
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
recursive: false
```

## Use a Remote ollama Instance

If ollama runs on another machine (e.g. a Tailscale peer):

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://100.x.x.x:11434
```

lgtmaybe adds no authentication, and ollama has none built in, so restrict
network access at the host or firewall.

## Inside the GitHub Action's Container

The Action runs lgtmaybe in a container, so an ollama server on the runner host
is reached at `host.docker.internal`, not `localhost`. Set it with the Action's
`provider`, `model` and `api_base` inputs, or in `.lgtmaybe.yml`:

```yaml
provider: ollama
model: qwen3.6:27b
api_base: http://host.docker.internal:11434
```

## Get Findings as JSON

The CLI prints a readable listing by default and never posts anywhere. Add
`--json` for a machine-readable array you can pipe into other tools:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434 \
  --json
```

## Let an AI Agent Apply the Fixes

`--format agent` prints the findings as correction instructions an AI coding
agent (such as Claude Code) can read and apply, so you can review and fix a
branch locally before opening a PR. See
[Fix findings with an AI agent](fix-findings-with-an-ai-agent.md).

## Concurrency on a Local Server

lgtmaybe fans its review calls out across a pool sized by `max_concurrency`,
6 by default on every provider, local included. That is a ceiling on what
lgtmaybe will have in flight, not a promise that your server runs them at once:

- **A default ollama runs one request at a time.** `OLLAMA_NUM_PARALLEL` is `1`
  unless you set it, so five of six calls queue (up to `OLLAMA_MAX_QUEUE`, 512).
  Nothing is lost and nothing fails; the wall clock is your server's throughput
  either way. Raising `max_concurrency` alone changes nothing.
- **The setting that matters is on the server.** Start ollama with
  `OLLAMA_NUM_PARALLEL=4` and four calls really do run together.
- **It costs memory in proportion.** ollama allocates the context window per
  parallel slot: four slots at `num_ctx` 32768 need 128k of context, not 32k.
  This is the usual reason a machine that reviewed happily with one slot falls
  over with four.

So the two settings should match. If you have raised the server, tell lgtmaybe:

```bash
lgtmaybe review --max-concurrency 4
```

**How to tell which you got.** `--profile` answers it: compare the per-call
`elapsed` column with the `review` stage total. If the calls' elapsed times sum
to roughly the stage time, they ran one after another; if they sum to well over
it, they overlapped.

```
review             95.32s        <- stage total
correctness         52.02s   |
spec                45.64s   |   sum is ~3x the stage,
artefacts           43.67s   |   so these overlapped
security            43.30s   |
```

**Queue time is already covered.** A queued call's timeout clock starts when the
request is sent, not when your server gets to it, so on a single-slot server the
last call in a six-wide fan-out would otherwise have to finish within the same
budget as the first. lgtmaybe scales the local default timeout by the fan-out
width to cover that (see [below](#slow-models-and-timeouts)), so you should not
need to intervene. If you would rather the calls didn't queue at all:

```yaml
# .lgtmaybe.yml
max_concurrency: 1
```

## The Output Ceiling (Why a Review Can't Run Forever)

A local model under structured output sometimes fails to stop and keeps
decoding. With no ceiling, only the per-call timeout ends it: half an hour of
sustained GPU for a single lens on a one-file diff, and hours for a whole review.

So a review runs with a default output ceiling of 16384 tokens per call, half
the default 32768 `num_ctx`. Measured findings payloads are hundreds of tokens,
so the rest is headroom for a thinking model, which draws its reasoning from the
same budget.

The same ceiling applies to `openai-compatible` and `openrouter`, because the
failure is not specific to local models: benchmarking found a model behind
`openai-compatible` emitting 223,558 tokens on a single lens call, and a hosted
model on `openrouter` returning a 393k-token response that parsed into 699 false
positives on a diff with nothing wrong in it. The first-party APIs (openai,
anthropic, zai, bedrock, vertex, azure) get no default ceiling, because they have
not shown this failure and a cap could truncate long findings payloads.

The ceiling is a fixed number, not a fraction of your window: raising `num_ctx`
for a big diff adds room for the prompt, not for a longer answer. Use
`max_tokens` when you want a longer answer.

Every run logs the ceiling it resolved and where it came from:

```
per-call budget resolved  timeout_s=3600  max_tokens=16384  max_tokens_source="provider default"
```

If a lens hits it, you get a truncation notice, not a silent clean review:

```
response hit the 16384-token `max_tokens` ceiling before finishing — the batch
is re-reviewed in smaller pieces automatically, so a lens that keeps doing it is
usually generation instability in the model, which a higher ceiling makes more
expensive rather than prevents
```

**One truncation is not a reason to raise the cap.** The batch is re-reviewed in
smaller pieces on its own, so a lens that trips it occasionally has still been
reviewed. A model that runs away repeatedly is generating past the ceiling, not
being cut short by it. A bigger ceiling only makes a longer wasted call; changing
the model is what fixes it.

Raising it is right when a genuinely long answer is being cut off: a large diff
with many real findings, where most are salvaged each time. Then raise it, or
remove the cap with `0`, which leaves the timeout as the run's only stop:

```bash
lgtmaybe review --provider ollama --model qwen3.5:4b --max-tokens 32768
lgtmaybe review --provider ollama --model qwen3.5:4b --max-tokens 0  # uncapped
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
max_tokens: 32768
```

Lowering the ceiling is the quickest way to get a result out of a slow model:
`--max-tokens 512` turns a stuck review into one that finishes in minutes and
reports what it truncated.

## Slow Models and Timeouts

Local models are slow, especially large ones on CPU, so ollama gets a long
default per-call timeout of 1800 seconds. You don't need to set anything for a
normal run. (Direct cloud providers default to 600 seconds.)

**That default scales with the fan-out.** Because the calls queue, the budget
has to cover the wait as well as the work. At the default width of six the
per-call timeout is `1800 × 6`, bounded by `max_review_seconds` (3600 seconds by
default; `0` disables the deadline and with it the bound). It is never trimmed
below ollama's own 1800 seconds, so a deadline set lower than that does not
shrink it. The bound applies to the budget, not the wall clock: the deadline
decides when a call may start, so a call that begins just inside it still runs
its full budget. At `max_concurrency: 1` there is no queue and the budget is the
plain 1800 seconds. Every run logs the number it resolved, and why:

```
per-call budget resolved  timeout_s=3600  timeout_source="provider default"  concurrency=6
```

An explicit `timeout` is never scaled: `timeout: 600` means 600 at any width.

If a big model still times out (you'll see
`litellm.Timeout: Connection timed out after 3600.0 seconds`), raise it
explicitly:

```bash
# CLI flag (seconds):
lgtmaybe review --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434 --timeout 3600
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
provider: ollama
model: qwen3.6:35b
timeout: 1800
```

The review makes four lens calls under the default `fast` preset (nine under
`--preset full`). On a default ollama those queue rather than overlap, so a slow
model takes roughly `lens calls × per-call time`. That is why `fast` is the
default: four calls instead of nine is the biggest single local speed-up.

To go faster, narrow the lenses with `categories:` in `.lgtmaybe.yml` (e.g. just
`security` and `correctness`), use a smaller model, or give ollama more GPU. If
you have the VRAM to serve requests in parallel, raise `OLLAMA_NUM_PARALLEL` on
the ollama server as described [above](#concurrency-on-a-local-server): the same
four calls then overlap instead of queueing, close to a 4× wall-clock win. Add
`--profile` to any run to see the per-call breakdown.

## Troubleshooting

**`Connection refused` on port 11434**: make sure `ollama serve` is running and
the `--api-base` URL is reachable.

**Model not found**: run `ollama pull <model>` before using it.

**`review incomplete — every review call failed`**: every lens call timed out or
returned output that wasn't valid JSON. Raise `--timeout`, try a model that
follows instructions more reliably, or check the `LITELLM_LOG=DEBUG` output for
the underlying error. lgtmaybe reports this and exits non-zero rather than
pretending the PR is clean.

For a large diff this can mean the prompt plus the findings don't fit in
ollama's context window, so the output is truncated. lgtmaybe runs ollama with a
32768-token context (`num_ctx`) and structured JSON output, which covers most
reviews.

lgtmaybe does not send ollama's `think` flag. ollama already turns thinking on
for a model that supports it and rejects the flag for one that does not, so
sending nothing is right for both.

For a big multi-file change, raise the context window with `--num-ctx` so the
whole diff and the findings fit. This is ollama-only; hosted providers manage
their context window server-side and ignore it:

```bash
# A large multi-file diff on a local model: more time and more context
# (32768 is already the default; go above it for very large diffs):
lgtmaybe review --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434 --timeout 1800 --num-ctx 65536
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
provider: ollama
model: qwen3.6:35b
timeout: 1800
num_ctx: 65536
```

`--num-ctx` needs enough RAM/VRAM on the ollama host, so size it to your
machine. The budget that decides when lgtmaybe splits a diff into separate model
calls is `--max-input-tokens`, which applies to any provider. Its default
(100000) is lowered automatically to fit the model's window; on ollama that is
`num_ctx` minus the output ceiling, so 16384 tokens at the defaults. Set it
explicitly to send a large diff in fewer calls, or lower it for a small-context
model. If a very large diff still truncates, narrow it with `include_paths` /
`exclude_paths` or a lower `max_files` in `.lgtmaybe.yml`, or run a model with a
bigger context window.

> **If you set `--max-input-tokens` yourself, keep it under `--num-ctx`.** The
> two are independent: `--max-input-tokens` caps each batch lgtmaybe sends,
> while `--num-ctx` is the window ollama allocates. An explicit value is used as
> given, not fitted. lgtmaybe also estimates tokens with a generic tokeniser and
> local models tokenise differently, so leave headroom: a batch budget well
> below the window (e.g. `--max-input-tokens 16000` with `--num-ctx 32768`)
> avoids ollama silently truncating the findings JSON, which otherwise shows up
> only as an unhelpful "review failed".

**Review is empty or truncated**: the diff may exceed the model's context
window. Add a path filter in `.lgtmaybe.yml` to shrink the diff, or lower
`max_files`.
