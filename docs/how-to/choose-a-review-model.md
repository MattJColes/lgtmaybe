---
description: Choose a cloud or local lgtmaybe review model using measured breadth, precision, false-positive, clean-change, and long-diff results.
---

# Choose a Review Model

The short version: among the hosted models, pick **`qwen/qwen3.8-max`** if you
want a quieter reviewer that is usually right, or **`z-ai/glm-5.2`** if catching
more issues matters more to you than extra noise. If the code can't leave your
hardware, **`nvidia/Qwen3.6-35B-A3B-NVFP4`** is the only local model with a
current measured result — it works, but it trails the hosted options.

The rest of this page explains where those recommendations come from, so you can
weigh the trade-offs yourself. The numbers are a snapshot from 19 August 2026;
the [lgtmaybe benchmark repository][bench] has the live leaderboard, complete
results, raw run records, and instructions for reproducing them.

## How to Read the Numbers

The benchmark plants known bugs in a set of code changes, asks each model to
review them, and checks what comes back:

- **Recall** — of the planted bugs, how many did the review find? Higher means
  fewer missed issues.
- **Precision** — of everything the model flagged, how much was a real planted
  bug? Higher means less noise.
- **Balanced F1** — a single score that combines recall and precision, so
  models can be ranked without favouring loud ones or quiet ones.
- **False positives** — findings that didn't match any planted bug. The
  benchmark deliberately assumes it knows about every real issue, so a
  plausible-looking finding outside the planted catalogue counts as false until
  a human reviews ("adjudicates") it.
- **Clean pass** — the benchmark includes changes verified to contain nothing
  worth flagging. Clean pass is how often the model correctly stayed quiet on
  them.

A score marked **provisional** means a small share of borderline findings is
still waiting on that human adjudication, so the number could shift slightly.
The runs cited below are 98–99% adjudicated. None of them has an immutable
audit trace yet — the live leaderboard reports that separately as `audit: no`.

## Choose a Cloud Model

| Model through OpenRouter | Balanced F1 | Recall | Precision | False positives | Clean pass |
|---|---|---|---|---|---|
| `qwen/qwen3.8-max` | 71.4% | 61.4% | 84.9% | 8 | 77.8% |
| `z-ai/glm-5.2` | 72.2% | 72.9% | 69.6% | 24 | 11.1% |
| `google/gemini-3.7-flash` | 62.2% | 54.3% | 72.7% | 15 | 44.4% |

The real contest is Qwen versus GLM — their overall scores overlap, but they
fail in opposite directions. GLM caught the most planted bugs, at the cost of
three times as many false positives, and it flagged something on almost every
verified-clean change. Qwen missed more bugs but was far more trustworthy when
it did speak up. Both results are provisional (Qwen has 1.9% of candidate
findings awaiting adjudication). On the separate long-diff suite, Qwen scored
71.7% with 75.0% recall.

Gemini scored lower across the board, though its result is the only fully
adjudicated one in this group. Its impressive-sounding 81.2% long-diff recall
came from an older lgtmaybe version (2.1.4), so it can't be compared with the
current runs.

```bash
lgtmaybe review --provider openrouter --model qwen/qwen3.8-max
```

Swapping in either of the other models is just a model-ID change — the provider
setup stays the same. See [Review with OpenRouter](review-with-openrouter.md)
for authentication and GitHub Action examples.

## Choose a Local Model

Only one local model has a published run under the current comparison
(lgtmaybe 2.2.0): **`nvidia/Qwen3.6-35B-A3B-NVFP4`**, served behind an
OpenAI-compatible server. It scored 57.1% balanced F1, with 47.1% recall, 72.3%
precision, 13 false positives, and a 44.4% clean pass rate — behind all three
hosted models. The result is provisional (2.0% awaiting adjudication), and a
single result is not enough to call a local leaderboard.

The long-diff suite also includes a smaller model,
**`RedHatAI/gemma-4-12B-it-FP8-Dynamic`**, which scored 40.5% with 37.5% recall
and 63.2% precision on lgtmaybe 2.2.0. It has no comparable current breadth
run, so those numbers can't be used to rank it against the local Qwen.

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

Because they measure different things, a breadth score and a long-horizon score
can't be compared with each other. Runs from different lgtmaybe versions can't
be ranked against each other either — changes to the prompt, parsing, or review
pipeline can move the result on their own.

## Source and Methodology

The figures above come from benchmark commit [`27392b1`][snapshot]. The
directly comparable breadth key is `breadth / canonical-breadth / lgtmaybe
2.2.0`; current long-horizon results use `long-horizon /
canonical-long-horizon / lgtmaybe 2.2.0`. For the record, the adjudication
coverage of the cited cloud Qwen, GLM, and local Qwen runs is 98.1%, 98.8%, and
98.0% respectively.

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
[snapshot]: https://github.com/MattJColes/lgtmaybe-benchmarks/tree/27392b1d796a86e757c33fcbe9c82505e6f0e945
