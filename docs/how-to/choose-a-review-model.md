---
description: Choose a cloud or local lgtmaybe review model using measured breadth, precision, false-positive, clean-change, and long-diff results.
---

# Choose a Review Model

Recommendations:

- **Hosted default: `qwen/qwen3.8-max`.** First on both suites, with the
  highest precision of any leading model and the fewest false positives.
- **Highest recall: `z-ai/glm-5.2`.** Finds the most planted bugs on breadth
  (72.9%), at three times Qwen's false positives. Its recall falls as the diff
  grows — 100% on the smallest long-horizon case down to 50% on the largest —
  so it suits repos with small PRs and reviewers willing to triage.
- **Fastest: `google/gemini-3.7-flash`.** Third on breadth, second on large
  diffs, and an order of magnitude faster than the leaders: 21 to 106 seconds
  per long-horizon case against Qwen's 616 to 1,355.
- **Local: `nvidia/Qwen3.6-35B-A3B-NVFP4`.** The highest-scoring local model
  with canonical runs on both suites, and the quietest of the mid-size local
  models. `Qwen/Qwen3.5-9B` scores marginally higher on breadth (48.5% against
  48.2%) with fewer false positives, but finds far less: 30.0% recall against
  47.1%, and 12.5% on large diffs. `unsloth/Qwen3.8-27B-NVFP4` posts the best
  local breadth score in the table (55.1%), but only on a diagnostic profile;
  its one canonical long-horizon run produced 347 false positives, which is
  the runaway-decode fault lgtmaybe 2.3.0 fixes. It needs a canonical re-run
  before it can be recommended.

The rest of this page shows every published run and the reasoning behind
these picks. The numbers are a snapshot from 5 September 2026; the
[lgtmaybe benchmark repository][bench] has the live leaderboard, complete
results, raw run records, and instructions for reproducing them.

## How to Read the Numbers

The benchmark plants known bugs in code changes, asks each model to review
them, and checks what comes back. It has two suites:

- **Breadth** measures everyday review quality: 32 small changes across
  seven programming languages, GitHub Actions, and Terraform, with 72
  planted findings spanning ten review lenses, plus nine verified-clean
  changes.
- **Long horizon** measures whether the model still finds bugs when the diff
  is very large: four defect-bearing Python changes that grow from about 3%
  to 90% of the input budget, plus one large clean change. Each plants the
  same eight bugs at the same relative positions, so recall differences come
  from size alone.

Each suite is scored separately, and a breadth score cannot be compared with a
long-horizon score: they measure different corpora. Compare breadth against
breadth and long horizon against long horizon, as the tables below do. Each
table reports:

- **Recall**: of the planted bugs, how many did the review find? Higher means
  fewer missed issues.
- **Precision**: of everything the model flagged, how much was a real planted
  bug? Higher means less noise.
- **Score**: balanced F0.5 scaled by completeness. F0.5 combines recall and
  precision but weights precision twice as heavily, so a model that flags less
  and is right more ranks above one that finds more bugs and adds noise.
  Breadth runs are the median of three repeats; the run-to-run ranges are in
  the repository.
- **Completeness**: the share of lens calls that returned parseable findings.
  Precision counts only findings that exist, so without this factor a run whose
  calls mostly failed would be scored on the few that survived. One published
  run failed 73.8% of its calls and would otherwise have outranked a run that
  found nearly twice as many bugs.
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

Ranked by score, best first:

| Model | lgtmaybe | Score | Completeness | Recall | Precision | False positives | Clean pass |
|---|---|---:|---:|---:|---:|---:|---:|
| `qwen/qwen3.8-max` | 2.2.0 | 67.0% | 84.9% | 61.4% | 84.9% | 8 | 77.8% |
| `openai/gpt-5.6-sol` | 2.1.4 | 55.8% | 80.8% | 58.6% | 72.1% | 17 | 33.3% |
| `google/gemini-3.7-flash` | 2.2.0 | 55.5% | 80.8% | 54.3% | 72.7% | 15 | 44.4% |
| `google/gemini-3.7-flash` | 2.1.4 | 54.8% | 79.6% | 48.6% | 77.8% | 10 | 55.6% |
| `z-ai/glm-5.2` | 2.2.0 | 53.2% | 75.1% | 72.9% | 69.6% | 24 | 11.1% |
| `minimax/minimax-m3` | 2.2.0 | 51.6% | 78.6% | 58.6% | 67.7% | 20 | 33.3% |
| `openai/gpt-5.4-nano` | 2.2.0 | 51.6% | 80.2% | 52.9% | 68.4% | 18 | 22.2% |
| `x-ai/grok-4.6` | 2.1.4 | 51.5% | 80.8% | 57.1% | 65.6% | 22 | 22.2% |
| `openai/gpt-5.6-luna` | 2.1.4 | 51.4% | 77.5% | 58.6% | 66.2% | 22 | 22.2% |
| `openai/gpt-5.6-terra` | 2.1.4 | 50.9% | 80.8% | 48.6% | 67.9% | 17 | 33.3% |
| `moonshotai/kimi-k3` | 2.1.4 | 49.9% | 80.8% | 65.7% | 60.5% | 32 | 0.0% |
| `google/gemini-3.1-pro-preview` | 2.1.4 | 49.9% | 80.8% | 55.7% | 63.5% | 24 | 22.2% |
| `z-ai/glm-5.3-flash` | 2.7.0 | 49.4% | 76.6% | 58.6% | 66.7% | 21 | 0.0% |
| `deepseek/deepseek-v4-pro-0813` | 2.2.0 | 48.2% | 76.0% | 40.0% | 72.7% | 12 | 44.4% |
| `kwaipilot/kat-coder-pro-v2.5` | 2.1.4 | 46.6% | 76.3% | 55.7% | 66.1% | 21 | 22.2% |
| `openai/gpt-5.4-mini` | 2.2.0 | 44.5% | 80.8% | 42.9% | 61.7% | 18 | 33.3% |
| `anthropic/claude-sonnet-5` | 2.2.0 | 43.3% | 93.1% | 17.1% | 81.2% | 3 | 88.9% |
| `moonshotai/kimi-k2.7-code` | 2.2.0 | 40.4% | 77.5% | 52.9% | 52.0% | 36 | 11.1% |
| `qwen/qwen3-coder-next` | 2.1.4 | 40.3% | 79.6% | 34.3% | 56.8% | 19 | 22.2% |
| `openai/gpt-oss-120b:nitro` | 2.1.4 | 39.9% | 80.8% | 57.1% | 47.7% | 45 | 11.1% |
| `mistralai/mistral-small-2603` | 2.1.4 | 39.4% | 73.7% | 62.9% | 51.1% | 46 | 33.3% |
| `anthropic/claude-sonnet-5` | 2.1.4 | 38.9% | 95.1% | 12.9% | 78.6% | 2 | 100.0% |
| `kwaipilot/kat-coder-air-v2.5` | 2.1.4 | 37.3% | 60.5% | 52.9% | 62.9% | 23 | 11.1% |
| `deepseek/deepseek-v4-flash-0731` | 2.2.0 | 34.8% | 65.3% | 50.0% | 59.0% | 25 | 33.3% |
| `z-ai/glm-4.7` | 2.1.4 | 30.9% | 52.0% | 52.9% | 61.3% | 25 | 44.4% |
| `anthropic/claude-opus-5` | 2.2.0 | 26.9% | 91.2% | 11.4% | 57.4% | 10 | 55.6% |
| `anthropic/claude-opus-5` | 2.1.4 | 12.0% | 98.5% | 2.9% | 71.4% | 1 | 100.0% |


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
- **`z-ai/glm-5.3-flash`.** Cheapest model in the table by an order of
  magnitude, with mid-pack recall and precision — but it flagged something on
  all nine clean changes (0.0% clean pass), so expect noise on green PRs.
- **`x-ai/grok-4.6` and `openai/gpt-5.6-luna`.** The widest run-to-run
  variance in the table: luna's three repeats spanned 52–66% F1.
- **`anthropic/claude-sonnet-5` and `claude-opus-5`.** Fail by staying
  silent rather than by being noisy: Sonnet 5 misses 5 of every 6 planted
  bugs, and Opus 5 found 2 of 70 on the 2.1.4 run. Not suited to this
  pipeline.

### Large diffs (long horizon)

Ranked by score, best first. The rows near the bottom mostly found bugs and
lost the ranking to noise: precision is weighted double, so a run with dozens
of false positives scores far below its recall alone would suggest.

| Model | lgtmaybe | Score | Completeness | Recall | Precision | False positives |
|---|---|---:|---:|---:|---:|---:|
| `qwen/qwen3.8-max` | 2.2.0 | 70.6% | 91.8% | 75.0% | 77.4% | 7 |
| `google/gemini-3.7-flash` | 2.1.4 | 64.8% | 85.7% | 81.2% | 74.3% | 9 |
| `kwaipilot/kat-coder-pro-v2.5` | 2.1.4 | 59.7% | 84.6% | 68.8% | 71.0% | 9 |
| `anthropic/claude-sonnet-5` | 2.1.4 | 54.1% | 93.8% | 56.2% | 58.1% | 13 |
| `x-ai/grok-4.6` | 2.1.4 | 53.3% | 90.0% | 84.4% | 55.1% | 22 |
| `deepseek/deepseek-v4-pro-0813` | 2.1.4 | 51.1% | 75.0% | 37.5% | 85.7% | 2 |
| `kwaipilot/kat-coder-air-v2.5` | 2.1.4 | 50.5% | 80.8% | 62.5% | 62.5% | 12 |
| `openai/gpt-5.6-terra` | 2.1.4 | 50.3% | 90.0% | 53.1% | 56.7% | 13 |
| `google/gemini-3.1-pro-preview` | 2.1.4 | 45.7% | 90.0% | 81.2% | 46.4% | 30 |
| `z-ai/glm-5.2` | 2.1.4 | 44.6% | 87.0% | 78.1% | 47.2% | 28 |
| `minimax/minimax-m3` | 2.1.4 | 41.5% | 74.2% | 53.1% | 56.7% | 13 |
| `openai/gpt-5.6-luna` | 2.1.4 | 41.3% | 88.5% | 78.1% | 42.4% | 34 |
| `deepseek/deepseek-v4-flash-0731` | 2.1.4 | 39.3% | 81.5% | 68.8% | 44.9% | 27 |
| `qwen/qwen3-coder-next` | 2.1.4 | 38.3% | 91.8% | 12.5% | 100.0% | 0 |
| `anthropic/claude-fable-5` | 2.1.4 | 35.4% | 75.5% | 46.9% | 46.9% | 17 |
| `moonshotai/kimi-k2.7-code` | 2.1.4 | 33.1% | 88.0% | 78.1% | 33.3% | 50 |
| `openai/gpt-5.6-sol` | 2.1.4 | 31.9% | 90.0% | 59.4% | 32.2% | 40 |
| `anthropic/claude-haiku-4.5` | 2.1.4 | 29.9% | 95.7% | 9.4% | 75.0% | 1 |
| `z-ai/glm-5.3-flash` | 2.7.0 | 27.5% | 43.2% | 87.5% | 59.6% | 19 |
| `moonshotai/kimi-k3` | 2.1.4 | 25.4% | 90.0% | 93.8% | 24.0% | 95 |
| `qwen/qwen3.8-max` | 2.1.4 | 16.7% | 85.5% | 71.9% | 16.5% | 116 |
| `z-ai/glm-4.7-flash` | 2.1.4 | 13.1% | 26.2% | 43.8% | 51.9% | 13 |
| `nvidia/nemotron-3.5-lightning` | 2.1.4 | 7.2% | 57.6% | 3.1% | 50.0% | 1 |
| `openai/gpt-oss-120b:nitro` | 2.1.4 | 6.8% | 74.1% | 31.2% | 7.8% | 118 |
| `openai/gpt-oss-20b` | 2.1.4 | 4.4% | 46.4% | 31.2% | 8.0% | 115 |
| `mistralai/mistral-small-2603` | 2.1.4 | 1.3% | 82.8% | 28.1% | 1.3% | 699 |
| `anthropic/claude-opus-5` | 2.1.4 | 0.0% | 100.0% | 0.0% | 100.0% | 0 |


**By model:**

- **`qwen/qwen3.8-max`.** 75% recall at every size up to a million input
  tokens, zero findings on the large clean change, and no truncated calls.
  It also shows how much the lgtmaybe version matters: the same model on
  2.1.4 scored 16.7% with 116 false positives; on 2.2.0 it posts 7.
- **`google/gemini-3.7-flash`.** Six points behind Qwen at a tenth of the
  wall-time, with 100% recall on the small case. It has not been re-run on
  2.2.0.
- **`kwaipilot/kat-coder-pro-v2.5`.** Holds 75% recall on every case past
  the small one.
- **`x-ai/grok-4.6`.** The highest recall that holds up as diffs grow: 87.5%
  at medium, large, and extra-large. Usable if someone will triage its
  output.
- **`z-ai/glm-5.2`.** Recall falls from 100% through 87.5% and 75% to 50% as
  the diff grows, while false positives double: strong on small changes,
  weak at scale.
- **`z-ai/glm-5.3-flash`.** Recall holds up (87.5%), but 43.2% completeness —
  over half its lens calls returned nothing parseable at 100k-token inputs —
  sinks the completeness-scaled score. Like its breadth run, it is far and
  away the cheapest row in the table.
- **The bottom of the table holds three different failures.** Some found
  bugs and lost the ranking to noise: kimi-k3 hit 93.8% recall, the highest
  in the corpus, with 95 false positives, and mistral-small posted 699. Some
  returned almost nothing: Opus 5 produced zero findings across all five
  cases, and `qwen3-coder-next` returned exactly one finding per case
  regardless of size, so their high precision reflects the absence of
  findings rather than quality. Some barely ran: `z-ai/glm-4.7-flash` shows
  26.2% completeness, meaning three-quarters of its lens calls returned
  nothing parseable, and `poolside/Laguna-XS-2.1-NVFP4` 17.2%. Read the
  completeness column before the score on any row near the bottom.

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

| Model | lgtmaybe | Score | Completeness | Recall | Precision | False positives | Clean pass |
|---|---|---:|---:|---:|---:|---:|---:|
| `unsloth/Qwen3.8-27B-NVFP4` † | 2.1.4 | 55.1% | 74.6% | 62.9% | 76.2% | 13 | 33.3% |
| `Qwen/Qwen3.5-9B` | 2.1.4 | 48.5% | 81.3% | 30.0% | 76.7% | 7 | 77.8% |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | 2.2.0 | 48.2% | 73.4% | 47.1% | 72.3% | 13 | 44.4% |
| `nvidia/Gemma-4-26B-A4B-NVFP4` | 2.1.4 | 39.4% | 61.7% | 72.9% | 62.7% | 31 | 0.0% |
| `RedHatAI/gemma-4-12B-it-FP8-Dynamic` | 2.2.0 | 39.1% | 77.5% | 44.3% | 51.6% | 31 | 11.1% |

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

| Model | lgtmaybe | Score | Completeness | Recall | Precision | False positives |
|---|---|---:|---:|---:|---:|---:|
| `unsloth/Qwen3.8-27B-NVFP4` † | 2.1.4 | 51.2% | 79.7% | 59.4% | 65.5% | 10 |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | 2.1.4 | 43.8% | 70.0% | 37.5% | 75.0% | 4 |
| `RedHatAI/gemma-4-12B-it-FP8-Dynamic` | 2.2.0 | 42.8% | 77.0% | 37.5% | 63.2% | 7 |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` † | 2.2.0 | 39.0% | 78.0% | 18.8% | 85.7% | 1 |
| `Qwen/Qwen3.5-9B` | 2.1.4 | 36.9% | 88.5% | 12.5% | 100.0% | 0 |
| `unsloth/Qwen3.8-27B-NVFP4` † | 2.2.0 | 26.9% | 79.7% | 75.0% | 29.6% | 57 |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | 2.2.0 | 10.1% | 58.2% | 15.6% | 17.9% | 23 |
| `nvidia/Gemma-4-26B-A4B-NVFP4` | 2.3.0 | 9.7% | 41.1% | 50.0% | 20.8% | 61 |
| `poolside/Laguna-XS-2.1-NVFP4` | 2.2.0 | 7.9% | 17.2% | 34.4% | 50.0% | 11 |
| `unsloth/Qwen3.8-27B-NVFP4` | 2.1.4 | 4.6% | 81.4% | 53.1% | 4.7% | 347 |
| `openai/gpt-oss-20b` † | 2.3.0 | 4.5% | 40.0% | 31.2% | 9.7% | 93 |
| `nvidia/Gemma-4-26B-A4B-NVFP4` | 2.1.4 | 4.5% | 43.1% | 34.4% | 8.8% | 114 |

**By model:**

- **`nvidia/Qwen3.6-35B-A3B-NVFP4`.** Modest recall, but the only local
  model whose recall rises as the diff grows (25% on the small case, 50% on
  the largest), with near-zero noise and nothing flagged on the clean case.
  Its 70.0% completeness is the weakest of the local models scoring above
  40%, so roughly a third of its lens calls return nothing usable. It is
  also slow: the large cases took over an hour each on the benchmark rig.
- **`unsloth/Qwen3.8-27B-NVFP4`.** The highest local breadth score in the
  table (55.1%), but every good result is on a diagnostic profile. Its three
  long-horizon runs differ wildly: 51.2% and 26.9% on diagnostic profiles,
  and 4.6% with 347 false positives on the only canonical one. That last run
  is the runaway-decode fault lgtmaybe 2.3.0 fixes, so a canonical re-run on
  2.3.0 is the number to wait for.
- **`poolside/Laguna-XS-2.1-NVFP4` and the Nemotron.** Both truncated on
  nearly every case: 17.2% and 58.2% completeness respectively, and the
  Nemotron used over a million output tokens per case. Not usable.
- **`Qwen/Qwen3.5-9B`.** Second on local breadth at a fraction of the size,
  and the quietest model in the corpus: 7 false positives on breadth and none
  at all on long horizon. That comes from flagging very little — 30.0% recall
  on breadth and 12.5% on large diffs — so it is a low-noise first pass
  rather than a thorough reviewer.
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

## Before Setting a Default

The corpus is synthetic. It says nothing about provider price, availability,
data handling, or how a model performs on your codebase. Shortlist a model or
two here, then try them on a few recent pull requests before setting a
team-wide default.

## Source and Methodology

The figures above come from benchmark commit [`d50d98c`][snapshot], and cover
every scoreable published run: the `canonical-breadth` and
`canonical-long-horizon` profiles across lgtmaybe 2.1.4, 2.2.0, 2.3.0 and
2.7.0, plus the
diagnostic-profile runs marked **†** (the benchmark publishes those for
investigation, since their settings differ from the canonical ones).

The [live benchmark repository][bench] provides:

- the current leaderboard and scoring method in its README;
- every completed run in [`RESULTS.md`][results], including the per-language
  and per-lens recall, the false-positive breakdowns, and the per-case size
  curves and wall-times the notes above draw on;
- append-only raw results under `results/raw/`; and
- commands for running the corpus against another model.

[bench]: https://github.com/MattJColes/lgtmaybe-benchmarks
[results]: https://github.com/MattJColes/lgtmaybe-benchmarks/blob/main/RESULTS.md
[snapshot]: https://github.com/MattJColes/lgtmaybe-benchmarks/commit/d50d98c813683582050f323140e1dfca004114e8
