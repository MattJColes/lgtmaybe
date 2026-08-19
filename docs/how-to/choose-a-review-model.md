---
description: Choose a cloud or local lgtmaybe review model using measured breadth, precision, false-positive, clean-change, and long-diff results.
---

# Choose a Review Model

The short version: among the hosted models, pick **`qwen/qwen3.8-max`** — near
the top for everyday review quality, the quietest of the leaders, and the best
measured result on very large diffs — or **`z-ai/glm-5.2`** if catching the
most issues matters more to you than extra noise. If the code can't leave your
hardware, **`nvidia/Qwen3.6-35B-A3B-NVFP4`** is the most consistent local model
across both suites, though the local field trails the hosted one.

The rest of this page shows every published run, so you can weigh the
trade-offs yourself. The numbers are a snapshot from 19 August 2026; the
[lgtmaybe benchmark repository][bench] has the live leaderboard, complete
results, raw run records, and instructions for reproducing them.

## How to Read the Numbers

The benchmark plants known bugs in a set of code changes, asks each model to
review them, and checks what comes back:

- **Recall** — of the planted bugs, how many did the review find? Higher means
  fewer missed issues.
- **Precision** — of everything the model flagged, how much was a real planted
  bug? Higher means less noise.
- **Breadth score (balanced F1)** — a single score that combines recall and
  precision, so models can be ranked without favouring loud ones or quiet
  ones.
- **Long-horizon score** — rewards recall but subtracts a penalty for every
  false positive. That penalty is why a noisy run can score 0% while still
  finding most of the bugs: the noise cancelled out the catches.
- **False positives** — findings that didn't match any planted bug. The
  benchmark deliberately assumes it knows about every real issue, so a
  plausible-looking finding outside the planted catalogue counts as false
  until a human reviews ("adjudicates") it.
- **Clean pass** — the breadth suite includes changes verified to contain
  nothing worth flagging. Clean pass is how often the model correctly stayed
  quiet on them.

Most breadth scores are **provisional**: a small share of borderline findings
(typically under 2%) is still waiting on human adjudication, so the numbers
can shift slightly. None of the runs has an immutable audit trace yet — the
live leaderboard reports that separately as `audit: no`.

The tables below compare all published breadth runs with each other, and all
published long-horizon runs with each other, across lgtmaybe versions. Each
row names the lgtmaybe version it ran on, because changes to the prompt,
parsing, or review pipeline can move a score on their own — where a model has
runs on both versions (Gemini 3.7 Flash, Claude Sonnet 5), the gap between
them gives a feel for how much. Runs marked **†** used a diagnostic
(non-standard) profile, so their settings differ from the rest; runs that
failed to produce a scoreable result are omitted here but kept in the
repository.

## Choose a Cloud Model

### Everyday review quality (breadth)

Ranked by balanced F1, best first:

| Model through OpenRouter | lgtmaybe | F1 | Recall | Precision | False positives | Clean pass |
|---|---|---:|---:|---:|---:|---:|
| `z-ai/glm-5.2` | 2.2.0 | 72.2% | 72.9% | 69.6% | 24 | 11.1% |
| `qwen/qwen3.8-max` | 2.2.0 | 71.4% | 61.4% | 84.9% | 8 | 77.8% |
| `openai/gpt-5.6-sol` | 2.1.4 | 64.5% | 58.6% | 72.1% | 17 | 33.3% |
| `moonshotai/kimi-k3` | 2.1.4 | 63.6% | 65.7% | 60.5% | 32 | 0.0% |
| `minimax/minimax-m3` | 2.2.0 | 62.8% | 58.6% | 67.7% | 20 | 33.3% |
| `google/gemini-3.7-flash` | 2.2.0 | 62.2% | 54.3% | 72.7% | 15 | 44.4% |
| `openai/gpt-5.6-luna` | 2.1.4 | 62.1% | 58.6% | 66.2% | 22 | 22.2% |
| `x-ai/grok-4.6` | 2.1.4 | 61.1% | 57.1% | 65.6% | 22 | 22.2% |
| `kwaipilot/kat-coder-pro-v2.5` | 2.1.4 | 60.5% | 55.7% | 66.1% | 21 | 22.2% |
| `google/gemini-3.7-flash` | 2.1.4 | 59.8% | 48.6% | 77.8% | 10 | 55.6% |
| `google/gemini-3.1-pro-preview` | 2.1.4 | 59.3% | 55.7% | 63.5% | 24 | 22.2% |
| `openai/gpt-5.4-nano` | 2.2.0 | 58.7% | 52.9% | 68.4% | 18 | 22.2% |
| `kwaipilot/kat-coder-air-v2.5` | 2.1.4 | 58.3% | 52.9% | 62.9% | 23 | 11.1% |
| `mistralai/mistral-small-2603` | 2.1.4 | 57.8% | 62.9% | 51.1% | 46 | 33.3% |
| `z-ai/glm-4.7` | 2.1.4 | 56.8% | 52.9% | 61.3% | 25 | 44.4% |
| `openai/gpt-5.6-terra` | 2.1.4 | 56.6% | 48.6% | 67.9% | 17 | 33.3% |
| `deepseek/deepseek-v4-flash-0731` | 2.2.0 | 54.1% | 50.0% | 59.0% | 25 | 33.3% |
| `moonshotai/kimi-k2.7-code` | 2.2.0 | 52.4% | 52.9% | 52.0% | 36 | 11.1% |
| `deepseek/deepseek-v4-pro-0813` | 2.2.0 | 52.0% | 40.0% | 72.7% | 12 | 44.4% |
| `openai/gpt-oss-120b:nitro` | 2.1.4 | 52.0% | 57.1% | 47.7% | 45 | 11.1% |
| `openai/gpt-5.4-mini` | 2.2.0 | 49.7% | 42.9% | 61.7% | 18 | 33.3% |
| `qwen/qwen3-coder-next` | 2.1.4 | 43.2% | 34.3% | 56.8% | 19 | 22.2% |
| `anthropic/claude-sonnet-5` | 2.2.0 | 28.3% | 17.1% | 81.2% | 3 | 88.9% |
| `anthropic/claude-sonnet-5` | 2.1.4 | 22.5% | 12.9% | 78.6% | 2 | 100.0% |
| `anthropic/claude-opus-5` | 2.2.0 | 18.2% | 11.4% | 57.4% | 10 | 55.6% |
| `anthropic/claude-opus-5` | 2.1.4 | 5.5% | 2.9% | 71.4% | 1 | 100.0% |

The real contest at the top is GLM versus Qwen — their overall scores overlap,
but they fail in opposite directions. GLM caught the most planted bugs, at the
cost of three times as many false positives, and it flagged something on
almost every verified-clean change. Qwen missed more bugs but was far more
trustworthy when it did speak up: the fewest false positives of any leader and
the best clean-pass rate.

The mid-table is crowded: a dozen models sit within a few points of each
other around the 55–65% mark, mostly trading a little recall for a little
precision. At the bottom, the Claude models show the opposite failure mode to
GLM's — they were quiet to a fault, producing very few findings (near-perfect
clean passes, but single-digit-to-low recall).

### Large diffs (long horizon)

Ranked by the long-horizon score, best first. Remember the score subtracts a
penalty per false positive — the 0% rows mostly found plenty of bugs and then
buried them in noise:

| Model through OpenRouter | lgtmaybe | Score | Recall | Precision | False positives |
|---|---|---:|---:|---:|---:|
| `qwen/qwen3.8-max` | 2.2.0 | 71.7% | 75.0% | 77.4% | 7 |
| `google/gemini-3.7-flash` | 2.1.4 | 71.7% | 81.2% | 74.3% | 9 |
| `kwaipilot/kat-coder-pro-v2.5` | 2.1.4 | 63.5% | 68.8% | 71.0% | 9 |
| `kwaipilot/kat-coder-air-v2.5` | 2.1.4 | 52.9% | 62.5% | 62.5% | 12 |
| `deepseek/deepseek-v4-pro-0813` | 2.1.4 | 50.5% | 37.5% | 85.7% | 2 |
| `x-ai/grok-4.6` | 2.1.4 | 47.5% | 84.4% | 55.1% | 22 |
| `anthropic/claude-sonnet-5` | 2.1.4 | 46.0% | 56.2% | 58.1% | 13 |
| `minimax/minimax-m3` | 2.1.4 | 43.4% | 53.1% | 56.7% | 13 |
| `openai/gpt-5.6-terra` | 2.1.4 | 43.4% | 53.1% | 56.7% | 13 |
| `z-ai/glm-4.7-flash` | 2.1.4 | 34.9% | 43.8% | 51.9% | 13 |
| `z-ai/glm-5.2` | 2.1.4 | 31.7% | 78.1% | 47.2% | 28 |
| `anthropic/claude-fable-5` | 2.1.4 | 29.8% | 46.9% | 46.9% | 17 |
| `google/gemini-3.1-pro-preview` | 2.1.4 | 29.7% | 81.2% | 46.4% | 30 |
| `deepseek/deepseek-v4-flash-0731` | 2.1.4 | 27.5% | 68.8% | 44.9% | 27 |
| `qwen/qwen3-coder-next` | 2.1.4 | 22.2% | 12.5% | 100.0% | 0 |
| `openai/gpt-5.6-luna` | 2.1.4 | 19.7% | 78.1% | 42.4% | 34 |
| `anthropic/claude-haiku-4.5` | 2.1.4 | 15.1% | 9.4% | 75.0% | 1 |
| `nvidia/nemotron-3.5-lightning` | 2.1.4 | 4.1% | 3.1% | 50.0% | 1 |
| `qwen/qwen3.8-max` | 2.1.4 | 0.0% | 71.9% | 16.5% | 116 |
| `moonshotai/kimi-k3` | 2.1.4 | 0.0% | 93.8% | 24.0% | 95 |
| `moonshotai/kimi-k2.7-code` | 2.1.4 | 0.0% | 78.1% | 33.3% | 50 |
| `openai/gpt-5.6-sol` | 2.1.4 | 0.0% | 59.4% | 32.2% | 40 |
| `openai/gpt-oss-120b:nitro` | 2.1.4 | 0.0% | 31.2% | 7.8% | 118 |
| `openai/gpt-oss-20b` | 2.1.4 | 0.0% | 31.2% | 8.0% | 115 |
| `mistralai/mistral-small-2603` | 2.1.4 | 0.0% | 28.1% | 1.3% | 699 |
| `anthropic/claude-opus-5` | 2.1.4 | 0.0% | 0.0% | 100.0% | 0 |

Two things stand out. Qwen3.8-max holds up on huge diffs — its 2.2.0 run leads
outright (and note how far its 2.1.4 run had fallen before the pipeline
improved, from 116 false positives down to 7). And GLM-5.2's high-recall,
high-noise style hurts it badly here: it still caught 78% of the bugs, but the
noise dragged its score to 31.7%. Gemini 3.7 Flash's strong 2.1.4 run and
kimi-k3's 93.8%-recall-but-zero-score run are the same lesson from opposite
ends — on large diffs, keeping the noise down matters as much as finding the
bugs.

```bash
lgtmaybe review --provider openrouter --model qwen/qwen3.8-max
```

Swapping in any other hosted model is just a model-ID change — the provider
setup stays the same. See [Review with OpenRouter](review-with-openrouter.md)
for authentication and GitHub Action examples.

## Choose a Local Model

The local models run behind an OpenAI-compatible server on your own hardware.
The field is smaller and scores lower than the hosted one, but the code never
leaves your machine and reviews cost nothing per call.

### Everyday review quality (breadth)

| Model | lgtmaybe | F1 | Recall | Precision | False positives | Clean pass |
|---|---|---:|---:|---:|---:|---:|
| `unsloth/Qwen3.8-27B-NVFP4` † | 2.1.4 | 70.6% | 62.9% | 76.2% | 13 | 33.3% |
| `nvidia/Gemma-4-26B-A4B-NVFP4` | 2.1.4 | 67.4% | 72.9% | 62.7% | 31 | 0.0% |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | 2.2.0 | 57.1% | 47.1% | 72.3% | 13 | 44.4% |
| `RedHatAI/gemma-4-12B-it-FP8-Dynamic` | 2.2.0 | 48.5% | 44.3% | 51.6% | 31 | 11.1% |
| `Qwen/Qwen3.5-9B` | 2.1.4 | 43.5% | 30.0% | 76.7% | 7 | 77.8% |

### Large diffs (long horizon)

| Model | lgtmaybe | Score | Recall | Precision | False positives |
|---|---|---:|---:|---:|---:|
| `unsloth/Qwen3.8-27B-NVFP4` † | 2.1.4 | 54.5% | 59.4% | 65.5% | 10 |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | 2.1.4 | 46.5% | 37.5% | 75.0% | 4 |
| `RedHatAI/gemma-4-12B-it-FP8-Dynamic` | 2.2.0 | 40.5% | 37.5% | 63.2% | 7 |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` † | 2.2.0 | 29.6% | 18.8% | 85.7% | 1 |
| `poolside/Laguna-XS-2.1-NVFP4` | 2.2.0 | 29.2% | 34.4% | 50.0% | 11 |
| `Qwen/Qwen3.5-9B` | 2.1.4 | 22.2% | 12.5% | 100.0% | 0 |
| `unsloth/Qwen3.8-27B-NVFP4` † | 2.2.0 | 0.0% | 75.0% | 29.6% | 57 |
| `unsloth/Qwen3.8-27B-NVFP4` | 2.1.4 | 0.0% | 53.1% | 4.7% | 347 |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | 2.2.0 | 0.0% | 15.6% | 17.9% | 23 |
| `nvidia/Gemma-4-26B-A4B-NVFP4` | 2.1.4 | 0.0% | 34.4% | 8.8% | 114 |

The two models that beat `nvidia/Qwen3.6-35B-A3B-NVFP4` on breadth both fall
apart elsewhere: the unsloth Qwen3.8-27B's best runs are diagnostic-profile
(†) ones, and its standard runs swing between 54.5% and a 347-false-positive
collapse; the Gemma-4-26B flagged something on every single clean change and
drowned the long-horizon suite in noise. The NVIDIA-served Qwen3.6-35B is the
model that scores respectably on both suites with sane noise levels — which is
why it's the local recommendation despite not topping either table.

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model nvidia/Qwen3.6-35B-A3B-NVFP4 \
  --api-base http://127.0.0.1:8000/v1
```

Budget more memory than the model weights alone — the context window and KV
cache need room too, and the serving engine, quantisation, context size, and
concurrency all affect local results. See
[Run locally with ollama](run-locally-with-ollama.md) for hardware guidance, or
[Other OpenAI-compatible servers](use-a-custom-openai-compatible-endpoint.md)
for vLLM, llama.cpp, and LM Studio setup.

## The Two Benchmark Suites

The scores above come from two suites that measure different things:

- **Breadth** asks: how good is the model at everyday review? It uses 32 small
  changes across seven programming languages, GitHub Actions, and Terraform,
  with 72 planted findings spanning ten review lenses plus nine verified-clean
  changes. Its balanced F1, recall, precision, false-positive count, and
  clean-pass rate are the headline quality numbers.
- **Long horizon** asks: does the model still catch bugs when the diff gets
  huge? It uses four defect-bearing Python changes that grow from about 3% to
  90% of the input budget, plus one large clean change, each planting the same
  eight bugs.

The two suites are scored by different formulas, so a breadth score and a
long-horizon score can't be compared with each other — always compare breadth
against breadth and long horizon against long horizon, as the tables above do.

## Source and Methodology

The figures above come from benchmark commit [`7699cb6`][snapshot], and cover
every scoreable published run: the `canonical-breadth` and
`canonical-long-horizon` profiles across lgtmaybe 2.1.4 and 2.2.0, plus the
diagnostic-profile runs marked **†** (the benchmark publishes those for
investigation, since their settings differ from the canonical ones).

The [live benchmark repository][bench] provides:

- the current leaderboard and scoring method in its README;
- every completed run, including per-language and per-lens recall, in
  [`RESULTS.md`][results];
- append-only raw results under `results/raw/`; and
- commands for running the corpus against another model.

One caveat before you commit: the corpus is synthetic. It says nothing about
provider price, availability, data handling, or how a model performs on your
codebase. Shortlist a model or two here, then try them on a few recent pull
requests before setting a team-wide default.

[bench]: https://github.com/MattJColes/lgtmaybe-benchmarks
[results]: https://github.com/MattJColes/lgtmaybe-benchmarks/blob/main/RESULTS.md
[snapshot]: https://github.com/MattJColes/lgtmaybe-benchmarks/tree/7699cb6f7e00a9073b1e8d92ca0f2872370c9664
