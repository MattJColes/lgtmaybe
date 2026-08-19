---
description: Choose a cloud or local lgtmaybe review model using measured breadth, precision, false-positive, clean-change, and long-diff results.
---

# Choose a Review Model

Recommendations:

- **Hosted default: `qwen/qwen3.8-max`.** Second on everyday review quality,
  the best precision among the leading models, and first on very large diffs.
  It is the only model near the top of both suites.
- **Highest recall: `z-ai/glm-5.2`.** Finds the most planted bugs, with three
  times Qwen's false positives, and its recall drops as diffs grow. Suits
  repos with small PRs.
- **Fastest: `google/gemini-3.7-flash`.** Mid-table on score, high precision,
  strong on large diffs, and an order of magnitude faster than the leaders.
- **Local: `nvidia/Qwen3.6-35B-A3B-NVFP4`.** The only local model with
  reasonable scores on both suites and low noise.

The rest of this page shows every published run and the reasoning behind
these picks. The numbers are a snapshot from 19 August 2026; the
[lgtmaybe benchmark repository][bench] has the live leaderboard, complete
results, raw run records, and instructions for reproducing them.

## How to Read the Numbers

The benchmark plants known bugs in a set of code changes, asks each model to
review them, and checks what comes back:

- **Recall**: of the planted bugs, how many did the review find? Higher means
  fewer missed issues.
- **Precision**: of everything the model flagged, how much was a real planted
  bug? Higher means less noise.
- **Breadth score (balanced F1)**: a single score combining recall and
  precision, used to rank the models. Breadth runs are the median of three
  repeats; the run-to-run ranges are in the repository.
- **Long-horizon score**: starts from recall and subtracts two points for
  every false positive, floored at zero. A noisy run can therefore score 0%
  even when it found most of the bugs. Long-horizon runs are single passes.
- **False positives**: findings that didn't match any planted bug. The
  benchmark deliberately assumes it knows about every real issue, so a
  plausible-looking finding outside the planted catalogue counts as false
  until a human reviews ("adjudicates") it. In practice the leading models'
  false positives are mostly noise on clean changes, near-miss findings, and
  duplicates; no leading model fell for the benchmark's planted cross-file
  traps.
- **Clean pass**: the breadth suite includes changes verified to contain
  nothing worth flagging. Clean pass is how often the model correctly stayed
  quiet on them.

Most breadth scores are **provisional**: a small share of borderline findings
(typically under 2%) is still waiting on human adjudication, so the numbers
can shift slightly. None of the runs has an immutable audit trace yet; the
live leaderboard reports that separately as `audit: no`.

The tables below compare all published breadth runs with each other, and all
published long-horizon runs with each other, across lgtmaybe versions. Each
row names the lgtmaybe version it ran on, because changes to the prompt,
parsing, or review pipeline can move a score on their own. Where a model has
runs on both versions (Gemini 3.7 Flash, Claude Sonnet 5), the gap between
them shows how much. Runs marked **†** used a diagnostic
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

Most models find the security and correctness plants at or near 100%, so the
ranking is decided by the other lenses (tests, documentation, complexity,
needless code) and by false-positive counts. Spec-delivery is the weakest
lens across the board: no model scores above 28.6% on it.

**By model:**

- **`qwen/qwen3.8-max`.** Its F1 is a statistical tie with GLM-5.2's (their
  three-repeat ranges overlap almost completely); the noise columns break
  the tie in Qwen's favour.
- **`z-ai/glm-5.2`.** Scores 100% on the correctness, security, performance,
  and test lenses, which no other model does. Pick it only if the team is
  prepared to triage its false positives.
- **`google/gemini-3.7-flash`.** The only fully adjudicated result in the
  table, and much faster than the leaders: its long-horizon cases finished
  in 21–107 seconds where Qwen's took 10–20 minutes. It misses most test and
  intent findings.
- **`openai/gpt-5.6-sol`.** Stable across its three repeats, but not yet
  re-run on 2.2.0. Of the OpenAI models that have been, the cheaper
  `gpt-5.4-nano` beats `gpt-5.4-mini` on every metric.
- **`kwaipilot/kat-coder-pro-v2.5`.** Mid-table here, but also holds up on
  large diffs (below).
- **`x-ai/grok-4.6` and `openai/gpt-5.6-luna`.** The widest run-to-run
  variance in the table: luna's three repeats spanned 52–66% F1.
- **`anthropic/claude-sonnet-5` and `claude-opus-5`.** Fail by staying
  silent rather than by being noisy: Sonnet 5 misses 5 of every 6 planted
  bugs, and Opus 5 found 2 of 70 on the 2.1.4 run. Not suited to this
  pipeline.

### Large diffs (long horizon)

Ranked by the long-horizon score, best first. The score subtracts two points
per false positive, so most of the 0% rows found bugs but lost the points to
noise:

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

This suite plants the same eight bugs in diffs that grow to ~90% of the input
budget, so how each model's recall changes with diff size matters as much as
the score itself.

**By model:**

- **`qwen/qwen3.8-max`.** 75% recall at every size up to a million input
  tokens, zero findings on the large clean change, and no truncated calls.
  It also shows how much the lgtmaybe version matters: the same model on
  2.1.4 scored 0% with 116 false positives; on 2.2.0 it posts 7.
- **`google/gemini-3.7-flash`.** Far shorter wall-times than Qwen at the
  same score, with 100% recall on the small case. It has not been re-run on
  2.2.0.
- **`kwaipilot/kat-coder-pro-v2.5`.** Holds 75% recall on every case past
  the small one.
- **`x-ai/grok-4.6`.** The highest recall that holds up as diffs grow: 87.5%
  at medium, large, and extra-large. Usable if someone will triage its
  output.
- **`z-ai/glm-5.2`.** Recall falls from 100% through 87.5% and 75% to 50% as
  the diff grows, while false positives double: strong on small changes,
  weak at scale.
- **The 0% rows fail in two ways.** Some found bugs and lost the score to
  noise: kimi-k3 hit 93.8% recall, the highest in the corpus, with 95 false
  positives, and mistral-small posted 699. Others returned almost nothing:
  Opus 5 produced zero findings across all five cases, and
  `qwen3-coder-next` returned exactly one finding per case regardless of
  size. 100% precision on those rows reflects the absence of findings, not
  quality.

```bash
lgtmaybe review --provider openrouter --model qwen/qwen3.8-max
```

Swapping in another hosted model only changes the model ID; the provider
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

**By model:**

- **`nvidia/Qwen3.6-35B-A3B-NVFP4`.** Its F1 sits between gpt-5.4-mini and
  gpt-5.4-nano in the hosted table, and it scores 100% on the security lens.
  Like the rest of the local field, it is weak on the test, documentation,
  and intent lenses.
- **`unsloth/Qwen3.8-27B-NVFP4`.** The best local F1, but that run used a
  diagnostic profile (†), and the same model's canonical long-horizon run
  produced 347 false positives. Wait for a canonical breadth run before
  relying on it.
- **`nvidia/Gemma-4-26B-A4B-NVFP4`.** Recall matches GLM-5.2's, but its
  long-horizon run produced 114 false positives. Only usable with a human
  triaging every finding.
- **`RedHatAI/gemma-4-12B-it-FP8-Dynamic`.** Completes both suites on a 12B
  footprint. Pick it only when the 35B does not fit in memory.
- **`Qwen/Qwen3.5-9B`.** Returned one finding per case on long horizon. Too
  small for review work.

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

**By model:**

- **`nvidia/Qwen3.6-35B-A3B-NVFP4`.** Modest recall, but the only local
  model whose recall rises as the diff grows (25% on the small case, 50% on
  the largest), with near-zero noise and nothing flagged on the clean case.
  It is slow: the large cases took over an hour each on the benchmark rig.
- **`unsloth/Qwen3.8-27B-NVFP4`.** Three runs with three very different
  results: 54.5% on a diagnostic profile, 347 false positives on the
  canonical run, and 57 false positives on a 2.2.0 rerun. The serving
  settings behind the good result have not been published.
- **`poolside/Laguna-XS-2.1-NVFP4` and the Nemotron.** Both truncated on
  nearly every case, and the Nemotron used over a million output tokens per
  case while scoring 0%. Not usable.
- **No local model handles very large diffs well.** If your PRs run large
  and must stay local, keep diffs small where you can and treat the review
  as a first pass rather than relying on it to catch everything.

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model nvidia/Qwen3.6-35B-A3B-NVFP4 \
  --api-base http://127.0.0.1:8000/v1
```

Budget more memory than the model weights alone: the context window and KV
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
- **Long horizon** asks: does the model still find bugs when the diff is very
  large? It uses four defect-bearing Python changes that grow from about 3% to
  90% of the input budget, plus one large clean change, each planting the same
  eight bugs at the same relative positions, so recall differences come from
  size alone.

The two suites use different scoring formulas, so a breadth score and a
long-horizon score cannot be compared with each other. Compare breadth
against breadth and long horizon against long horizon, as the tables above do.

## Source and Methodology

The figures above come from benchmark commit [`7699cb6`][snapshot], and cover
every scoreable published run: the `canonical-breadth` and
`canonical-long-horizon` profiles across lgtmaybe 2.1.4 and 2.2.0, plus the
diagnostic-profile runs marked **†** (the benchmark publishes those for
investigation, since their settings differ from the canonical ones).

The [live benchmark repository][bench] provides:

- the current leaderboard and scoring method in its README;
- every completed run in [`RESULTS.md`][results], including the per-language
  and per-lens recall, the false-positive breakdowns, and the per-case size
  curves and wall-times the notes above draw on;
- append-only raw results under `results/raw/`; and
- commands for running the corpus against another model.

The corpus is synthetic. It says nothing about provider price, availability,
data handling, or how a model performs on your codebase. Shortlist a model or
two here, then try them on a few recent pull requests before setting a
team-wide default.

[bench]: https://github.com/MattJColes/lgtmaybe-benchmarks
[results]: https://github.com/MattJColes/lgtmaybe-benchmarks/blob/main/RESULTS.md
[snapshot]: https://github.com/MattJColes/lgtmaybe-benchmarks/tree/7699cb6f7e00a9073b1e8d92ca0f2872370c9664
