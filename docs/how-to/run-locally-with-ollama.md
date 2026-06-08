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

## Run the review

From inside the repo, on the branch you want reviewed:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

This diffs your current branch against the default branch and prints the
findings. Add `--working` to review only your uncommitted edits, or `--base <ref>`
to diff against a different base.

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

**`review incomplete — the model returned no usable output`** — every category
call timed out or returned output that wasn't valid JSON. Raise `--timeout`, try a
model that follows instructions more reliably, or check `LITELLM_LOG=DEBUG` output
for the underlying error. lgtmaybe reports this (and exits non-zero) rather than
pretending the PR is clean.

For a **large diff** this can mean the prompt plus the findings don't fit in
ollama's context window and the output gets truncated. lgtmaybe runs ollama with
a generous context (`num_ctx` of 16384) and **structured JSON output** (it also
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

**Review is empty or truncated** — the diff may exceed the model's context
window. Add a path filter in `.lgtmaybe.yml` to reduce diff size, or set
`max_files` to a lower value.
