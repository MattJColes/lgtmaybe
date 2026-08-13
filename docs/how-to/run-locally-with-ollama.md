---
description: Run lgtmaybe entirely locally with an ollama model — zero API cost, zero egress, no keys, and code never leaves your machine.
---

# Run Locally with ollama

Use this guide to review your local changes with a local ollama model — zero API
cost, zero egress, no keys required. The CLI reviews your `git` diff and prints
the findings; to post reviews on real pull requests, use the
[GitHub Action](use-as-github-action.md).

> **ollama is the easiest local start.** Running a different local server —
> **vLLM, llama.cpp, or LM Studio** — or a hosted OpenAI-compatible API like
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

## Pull the model you want

```bash
ollama pull qwen3.6:27b        # strong all-round coding model
ollama pull gemma4:e4b         # smaller — for devices with limited RAM
```

List available models:

```bash
ollama list
```

## Which model, and will it fit?

Two simple rules:

1. **Pick a coding model.** Reviewing a PR is a coding task, so use a model built
   for code (e.g. the Qwen3 coder line), not a general chat model. Models are
   tuned for different jobs — match the model to the use case.
2. **Bigger and newer is more accurate.** Use the largest, most recent coding
   model your hardware can run. Our accuracy numbers are for a *small* model —
   we benchmarked **qwen3.5:4b**, and it did well, but only *with recursive
   review on* (88% vs 61% recall). A larger, newer model catches more across the
   board and leans on that trick less.

A solid mid-2026 default is **Qwen3.6-27B** (`qwen3.6:27b`): near frontier API
models on coding benchmarks (SWE-bench Verified ~77%) yet small enough to run on a
workstation or a well-specced laptop, so it clears lgtmaybe's bar across all the
review lenses without a data-center GPU. Smaller models work too — accuracy just
falls off (you'll miss subtler findings and may need `--no-reflect`, because the
reflection pass over-prunes on a weak model).

**Hardware, quantised (the usual way to run it locally):**

| You have | What to expect |
|---|---|
| **< 32 GB** RAM/VRAM | Drop to a smaller model (`gemma4:e4b`) or route to a hosted provider — 27B at a usable quant won't leave room for the diff. |
| **32 GB** RAM/VRAM | The practical floor. Run `qwen3.6:27b` at a 4-bit quant (≈16–18 GB of weights) with a modest context window. Keep `num_ctx` conservative so the model plus the diff and findings fit. |
| **48 GB+** RAM/VRAM (preferred) | Comfortable. Room for the weights plus the default `--num-ctx` (32k) — or a raised 64k window for very large diffs, with headroom for the KV cache. |

This applies to both discrete VRAM and Apple-Silicon unified memory. A bigger
context window costs memory on top of the weights, so if you bump `--num-ctx` for
a large diff (see [Slow models and timeouts](#slow-models-and-timeouts)), size it
to the table above. On a hosted provider none of this matters — the model runs on
the provider's hardware.

## Run the review

From inside the repo, on the branch you want reviewed:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

This diffs your current branch against the remote primary branch and prints the
findings. Add `--working` to review the whole worktree (branch commits plus
uncommitted edits) against that same base, `--uncommitted` to review only your
uncommitted edits against HEAD, or `--base <ref>` to diff against a different
base.

## Reviewing large files (recursive walk)

When a single file's diff is larger than the per-call token budget
(`--max-input-tokens`, default 100000), lgtmaybe **walks it hunk-by-hunk** —
each hunk reviewed in its own focused call — instead of sending the whole file at
once and letting the tail drop out of the model's attention. The findings are
merged back together, and inline-comment positions still bind to the real diff.
This **RLM-style recursive review is on by default** (`recursive: true`); files
that already fit the budget are still reviewed whole, so nothing changes for small
diffs.

It helps **small local models the most**, because a smaller, focused prompt is
easier to review thoroughly. In our A/B benchmark a local **qwen3.5:4b** caught
**all 6** planted bugs reviewing recursively versus **4/6** reviewing each file
whole — the two it missed whole were both in the file's *tail*, even though the
diff fit the context window (so the gain is focus, not just avoiding truncation).
It's a single non-deterministic run on one fixture, so treat it as directional;
the harness behind it is in
[DEVELOPMENT.md](https://github.com/MattJColes/lgtmaybe/blob/main/DEVELOPMENT.md#benchmarking-the-recursive-rlm-walk).

To use the **original whole-file method** instead — one call per file, which keeps
all of a file's hunks in view together but tends to miss more on big files with
small models — pass `--no-recursive`:

```bash
lgtmaybe review --provider ollama --model qwen3.5:4b \
  --api-base http://localhost:11434 --no-recursive
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
recursive: false
```

## Use a remote ollama instance

If ollama runs on another machine (e.g. a Tailscale peer):

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://100.x.x.x:11434
```

No authentication is added — ollama has no built-in auth. Ensure network access
is restricted at the host or firewall level.

## Inside the GitHub Action's container

The Action runs lgtmaybe in a container, so ollama on the runner host is reached
at `host.docker.internal` rather than `localhost`. Set it in `.lgtmaybe.yml`,
since the Action reads its provider settings from config:

```yaml
provider: ollama
model: qwen3.6:27b
api_base: http://host.docker.internal:11434
```

## Get findings as JSON

The CLI prints a readable listing by default and never posts anywhere. Add
`--json` for a machine-readable array you can pipe into other tooling:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434 \
  --json
```

## Let an AI agent apply the fixes

`--format agent` prints the findings as correction instructions an AI coding
agent (such as Claude Code) can read and apply, so you can review and fix a
branch locally before opening a PR. See
[Fix findings with an AI agent](fix-findings-with-an-ai-agent.md).

## Concurrency on a local server

lgtmaybe fans its review calls out across a pool sized by `max_concurrency`,
**6 by default on every provider, local included**. That is a ceiling on what
lgtmaybe will have in flight — it is not a promise your server will run them at
once, and the distinction matters:

- **A default ollama runs one at a time.** `OLLAMA_NUM_PARALLEL` is `1` unless you
  set it, so five of six calls simply queue (up to `OLLAMA_MAX_QUEUE`, 512). They
  are not lost and nothing fails; the wall clock is your server's throughput
  either way. Raising `max_concurrency` alone therefore changes nothing.
- **The knob that matters is on the server.** Start ollama with
  `OLLAMA_NUM_PARALLEL=4` and four calls genuinely run together.
- **It costs memory, in proportion.** Ollama allocates the context window *per
  parallel slot*: four slots at `num_ctx` 32768 needs 128k of context allocated,
  not 32k. This is the usual reason a machine that reviewed happily at one slot
  falls over at four.

So the two settings want to match. If you have raised the server, tell lgtmaybe:

```bash
lgtmaybe review --max-concurrency 4
```

**How to tell which you got.** `--profile` already answers it, no extra flag
needed: compare the per-call `elapsed` column against the `review` stage total.
If the calls' elapsed times sum to roughly the stage time, they ran one after
another; if they sum to well over it, they genuinely overlapped.

```
review             95.32s        <- stage total
correctness         52.02s   |
spec                45.64s   |   sum is ~3x the stage,
artefacts           43.67s   |   so these overlapped
security            43.30s   |
```

**Queue time is already paid for.** A queued call's timeout clock starts when the
request is *sent*, not when your server gets to it, so on a single-slot box the
last call in a six-wide fan-out would otherwise have to be served within the same
budget as the first. lgtmaybe scales the local default by the fan-out width to
cover that (see below), so you should not need to intervene. If you would rather
not have the calls queue at all:

```yaml
# .lgtmaybe.yml
max_concurrency: 1
```

## The output ceiling (why a review can't run forever)

A local model under structured output sometimes fails to stop: the response just
keeps decoding. With no ceiling the only thing that ends it is the per-call
timeout — half an hour of sustained GPU for a single lens on a one-file diff, and
hours for a whole review.

So an ollama review runs with a **default output ceiling of 8192 tokens per
call** — a quarter of the default 32768 `num_ctx`. Measured findings payloads are
hundreds of tokens, so the rest is headroom for a thinking model — reasoning is
drawn from this same budget. Hosted providers get no default ceiling; they don't
have this problem, and capping them would truncate long findings for nothing.

The ceiling is a fixed number, not a fraction of your window: raising `num_ctx`
for a big diff buys room for the **prompt**, not for a longer answer. Use
`max_tokens` when you want a longer answer.

Every run says which ceiling it resolved and where it came from:

```
per-call budget resolved  timeout_s=3600  max_tokens=8192  max_tokens_source="provider default"
```

If a lens does hit it you get a **truncation notice**, not a silent clean review:

```
response truncated at the 8192-token `max_tokens` ceiling — the batch is
re-reviewed in smaller pieces automatically, so a lens that keeps doing it is
usually generation instability in the model, which a higher ceiling makes more
expensive rather than prevents
```

**One truncation is not a reason to raise the cap.** The batch is re-reviewed in
smaller pieces on its own, so a lens that trips it occasionally has still been
reviewed. A model that runs away repeatedly is generating past the ceiling rather
than being cut short by it — a bigger ceiling just buys a longer wasted call, and
a different model is the lever that moves it.

Where a raise *is* right is a genuinely long answer being cut off — a large diff
with many real findings, salvaging most of them each time. Then raise it, or turn
the cap off entirely with `0`, which puts the run back on the timeout as its only
stop:

```bash
lgtmaybe review --provider ollama --model qwen3.5:4b --max-tokens 16384
lgtmaybe review --provider ollama --model qwen3.5:4b --max-tokens 0  # uncapped
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
max_tokens: 16384
```

Going the other way is the fastest lever there is on a slow model: a low ceiling
(`--max-tokens 512`) turns a stuck review into one that finishes in minutes and
tells you what it truncated.

## Slow models and timeouts

Local models are slow, especially large ones on CPU, so lgtmaybe gives **ollama a
long default per-request timeout (1800 seconds)** automatically — you don't need
to set anything for a normal run. (Direct cloud providers default to 600 s.)

**That default scales with the fan-out.** Because the calls queue, the budget has
to cover the wait as well as the work: at the default width of six the resolved
per-call timeout is `1800 × 6`, **bounded by `max_review_seconds`** (3600 s by
default; `0` disables the deadline and with it the cap) — though never trimmed
below ollama's own 1800 s, so a deadline set under that does not shrink it. (And it bounds the budget rather than the wall clock: the
deadline decides when a call may *start*, so one that begins just inside it still
runs its budget out.) At
`max_concurrency: 1` there is no queue and the budget is the plain 1800 s. Every
run logs which number it resolved, and why:

```
per-call budget resolved  timeout_s=3600  timeout_source="provider default"  concurrency=6
```

An explicit `timeout` is never scaled — `timeout: 600` means 600 at any width.

If a big model still times out — you'll see
`litellm.Timeout: Connection timed out after 3600.0 seconds` — raise it explicitly:

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

The review fans out **four calls** under the default `fast` preset (nine under
`--preset full`). On a default ollama those queue rather than overlap, so a slow
model takes roughly `lens calls × per-call time` — which is exactly why `fast` is
the default: four calls instead of nine is the single biggest local speed-up.

To go faster still, narrow the lenses with `categories:` in `.lgtmaybe.yml`
(e.g. just `security` and `correctness`), use a smaller model, or give ollama
more GPU. If you have the VRAM to truly serve requests in parallel, raise
`OLLAMA_NUM_PARALLEL` on the **ollama server** as described above — the same four
calls then overlap instead of queueing, which is close to a 4× wall-clock win.
Add `--profile` to any run to see the per-call breakdown.

## Troubleshooting

**`Connection refused` on port 11434** — ensure `ollama serve` is running and
the `--api-base` URL is reachable.

**Model not found** — run `ollama pull <model>` before using it.

**`review incomplete — every review call failed`** — every category
call timed out or returned output that wasn't valid JSON. Raise `--timeout`, try a
model that follows instructions more reliably, or check `LITELLM_LOG=DEBUG` output
for the underlying error. lgtmaybe reports this (and exits non-zero) rather than
pretending the PR is clean.

For a **large diff** this can mean the prompt plus the findings don't fit in
ollama's context window and the output gets truncated. lgtmaybe runs ollama with
a generous context (`num_ctx` of 32768) and **structured JSON output**, which
covers most reviews.

lgtmaybe does not pass ollama's `think` flag either way. Ollama already defaults
thinking **on** for a model that supports it and rejects the flag outright for one
that does not, so sending nothing is the only choice that is right for both.
lgtmaybe used to force it off — that made reasoning models review with their
reasoning switched off, which measurement says is the single biggest lever on
finding quality there is.

For a big multi-file change ("vibe-coded" commits across many files), raise the
context window with `--num-ctx` so the whole diff and the findings fit — this is
**ollama-only** (hosted providers manage their context window server-side and
ignore it):

```bash
# A large multi-file diff on a local model — more time and more context
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

`--num-ctx` needs enough RAM/VRAM on the ollama host — a bigger window costs
memory, so size it to your machine. The token budget that decides when lgtmaybe
splits a diff into separate model calls is `--max-input-tokens` (default 100000),
which applies to **any** provider — raise it to send a large diff in fewer calls,
lower it for a small-context model. If a very large diff still truncates, narrow
it with `include_paths` / `exclude_paths` or a lower `max_files` in `.lgtmaybe.yml`,
or run a model with a bigger context window.

> **Keep `--max-input-tokens` under `--num-ctx`.** The two are independent:
> `--max-input-tokens` caps each batch lgtmaybe *sends*, while `--num-ctx` is the
> window ollama actually *allocates*. lgtmaybe estimates tokens with a generic
> tokenizer, and local models tokenize differently, so leave headroom — a batch
> budget comfortably below your context window (e.g. `--max-input-tokens 24000`
> with `--num-ctx 32768`) avoids ollama silently truncating the findings JSON,
> which otherwise surfaces only as an unhelpful "review failed".

**Review is empty or truncated** — the diff may exceed the model's context
window. Add a path filter in `.lgtmaybe.yml` to reduce diff size, or set
`max_files` to a lower value.
