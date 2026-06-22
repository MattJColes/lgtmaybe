# Run Locally with ollama

Use this guide to review your local changes with a local ollama model — zero API
cost, zero egress, no keys required. The CLI reviews your `git` diff and prints
the findings; to post reviews on real pull requests, use the
[GitHub Action](use-as-github-action.md).

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
| **48 GB+** RAM/VRAM (preferred) | Comfortable. Room for the weights plus a generous `--num-ctx` (32k) for big multi-file diffs, with headroom for the KV cache. |

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

## Slow models and timeouts

Local models are slow, especially large ones on CPU, so lgtmaybe gives **ollama a
long default per-request timeout (300 seconds)** automatically — you don't need
to set anything for a normal run. (Cloud providers default to 60 s.)

If a big model still times out — you'll see
`litellm.Timeout: Connection timed out after 300.0 seconds` — raise it explicitly:

```bash
# CLI flag (seconds):
lgtmaybe review --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434 --timeout 900
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
provider: ollama
model: qwen3.6:35b
timeout: 900
```

The review fans out one call per category. lgtmaybe runs those **serially for
ollama** (a single ollama instance serves one request at a time, so firing them
concurrently would only make each wait and time out). The trade-off is wall-clock
time — a slow model takes roughly `categories × per-call time`. To go faster,
narrow the lenses with `categories:` in `.lgtmaybe.yml` (e.g. just `security` and
`correctness`), use a smaller model, or give ollama more GPU. If you have the VRAM
to truly serve requests in parallel, raise `OLLAMA_NUM_PARALLEL` on the **ollama
server** — lgtmaybe still issues ollama calls one at a time, but a faster server
shortens each.

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
a generous context (`num_ctx` of 32768) and **structured JSON output** (it also
disables "thinking" so reasoning models like qwen3.x emit the findings directly),
which covers most reviews.

For a big multi-file change ("vibe-coded" commits across many files), raise the
context window with `--num-ctx` so the whole diff and the findings fit — this is
**ollama-only** (hosted providers manage their context window server-side and
ignore it):

```bash
# A large multi-file diff on a local model — more time and more context:
lgtmaybe review --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434 --timeout 900 --num-ctx 32768
```

```yaml
# or in .lgtmaybe.yml (also how the GitHub Action picks it up):
provider: ollama
model: qwen3.6:35b
timeout: 900
num_ctx: 32768
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
