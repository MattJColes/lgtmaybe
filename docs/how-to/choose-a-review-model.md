---
description: Choose a cloud or local lgtmaybe review model using measured breadth, precision, false-positive, clean-change, and long-diff results.
---

# Choose a Review Model

The calls, up front:

- **Default hosted pick: `qwen/qwen3.8-max`.** The only model near the top of
  both suites — second on everyday review quality with by far the best
  precision among the leaders, and first outright on very large diffs.
- **Maximum catches: `z-ai/glm-5.2`.** Finds the most planted bugs of any
  model, at the cost of three times Qwen's noise — and its recall falls apart
  as diffs grow, so keep it for repos with small PRs.
- **Fast and frugal: `google/gemini-3.7-flash`.** Mid-table on score but high
  precision, strong on large diffs, and an order of magnitude faster than the
  leaders.
- **Local pick: `nvidia/Qwen3.6-35B-A3B-NVFP4`.** Not top of either local
  table, but the only local model that scores respectably on both suites with
  sane noise levels.

The rest of this page shows every published run and the reasoning, so you can
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
- **Breadth score (balanced F1)** — a single score that combines recall and
  precision, so models can be ranked without favouring loud ones or quiet
  ones. Breadth runs are the median of three repeats; the run-to-run ranges
  are in the repository.
- **Long-horizon score** — starts from recall and subtracts two points for
  every false positive, floored at zero. That penalty is why a noisy run can
  score 0% while still finding most of the bugs: the noise cancelled out the
  catches. Long-horizon runs are single passes.
- **False positives** — findings that didn't match any planted bug. The
  benchmark deliberately assumes it knows about every real issue, so a
  plausible-looking finding outside the planted catalogue counts as false
  until a human reviews ("adjudicates") it. In practice the leaders' false
  positives are mostly noise on clean changes, near-miss findings, and
  duplicates — no leading model fell for the benchmark's planted cross-file
  traps.
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

Start with what the whole table agrees on: every serious model catches the
security and correctness plants at or near 100%. The ranking is decided by the
softer lenses — tests, documentation, complexity, needless code — and by how
much noise a model produces getting there. (One lens humbles everyone: no
model scores above 28.6% on spec-delivery findings.)

**The calls:**

- **`qwen/qwen3.8-max` — the default.** Its headline F1 is a statistical tie
  with GLM's (their three-repeat ranges overlap almost completely), so
  behaviour decides — and the behaviours could not be more different. Qwen
  posts a quarter as many false positives, stays quiet on 7 of 9 clean
  changes, and is right about 85% of the time when it speaks. A reviewer the
  team stops trusting is worse than one that occasionally misses, which is
  why the tie breaks to Qwen.
- **`z-ai/glm-5.2` — when catches matter most.** A perfect 100% on the
  correctness, security, performance, *and* test lenses — no other model
  manages that. The price: 24 false positives and something flagged on 8 of 9
  clean changes. Pick it if your team will happily dismiss noise to miss
  nothing; skip it if a chatty bot will get muted.
- **`google/gemini-3.7-flash` — the value pick.** Mid-table on F1, but with
  leader-grade precision, the only fully adjudicated result in the table, and
  wall-times an order of magnitude faster than the leaders (its long-horizon
  cases finished in 21–107 seconds; Qwen's took 10–20 minutes). If you review
  every push, or pay per token, this is the sweet spot. Its blind spots are
  the softer lenses — tests and intent findings in particular.
- **`openai/gpt-5.6-sol` — the best OpenAI showing**, third overall on the
  older 2.1.4 run with a tight, stable range. It hasn't been re-run on 2.2.0
  yet; the OpenAI models that have — `gpt-5.4-nano` and `gpt-5.4-mini` — land
  midfield, and the cheaper nano oddly beats mini on every metric.
- **`minimax/minimax-m3` and `kwaipilot/kat-coder-pro-v2.5` — solid
  midfielders.** Nothing spectacular, no glaring vice; kat-coder-pro in
  particular backs it up with a strong large-diff result (below), which makes
  it the sleeper pick of the mid-table.
- **`x-ai/grok-4.6` and `openai/gpt-5.6-luna` — inconsistent.** Decent
  medians, but the widest run-to-run swings in the table (luna's three
  repeats spanned 52–66%). You may not get the review quality you sampled.
- **The noisy tier — avoid.** `moonshotai/kimi-k3` and `kimi-k2.7-code`,
  `mistralai/mistral-small-2603`, and `openai/gpt-oss-120b`: respectable
  recall, but precision at or below 60%, 32–46 false positives, and clean
  passes of 0–11%. These will bury their catches in noise.
- **The DeepSeek pair — no reason to pick either.** `deepseek-v4-pro` is
  precise but misses 60% of the plants; `deepseek-v4-flash` is midfield with
  midfield noise. Both are dominated by something above them.
- **The Claude models — wrong tool for this job.** The opposite failure mode
  to the noisy tier: nearly silent. Sonnet 5 has the best precision-and-clean
  discipline in the table and misses 5 of every 6 planted bugs; Opus 5 found
  2 of 70 on the 2.1.4 run. Whatever these models are optimising for, it is
  not exhaustive diff review through this pipeline.

### Large diffs (long horizon)

Ranked by the long-horizon score, best first. Remember the score subtracts two
points per false positive — the 0% rows mostly found plenty of bugs and then
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

This suite grows the same eight bugs from a small diff to one filling ~90% of
the input budget, so the interesting question isn't the score — it's the shape
of each model's recall as the diff grows.

**The calls:**

- **`qwen/qwen3.8-max` — the big-diff pick.** Dead-flat 75% recall at every
  size up to a million input tokens, zero findings on the large clean change,
  no truncated calls. It is also the poster child for why lgtmaybe versions
  matter: the same model on 2.1.4 scored 0% with 116 false positives; the
  2.2.0 pipeline run posts 7. If your PRs run large, this plus the current
  lgtmaybe is the proven combination.
- **`google/gemini-3.7-flash` — the co-leader, and much faster.** Ties Qwen's
  score with higher recall (81.2%, including 100% on the small case) on the
  older pipeline, at a tiny fraction of the wall-time. It hasn't been re-run
  on 2.2.0 yet — given what that pipeline did for Qwen, it may well lead when
  it is.
- **`kwaipilot/kat-coder-pro-v2.5` — a legitimate third**, holding 75% recall
  once past the small case with modest noise. Consistent with its solid
  breadth showing: the most underrated model in the corpus.
- **`x-ai/grok-4.6` — maximum recall, if you can stand it.** The best
  bug-finding that *survives* size — 87.5% recall at medium, large, and
  extra-large — but 22 false positives, including three on the clean change,
  cap its score. The choice for "miss nothing on big diffs, we'll triage".
- **`z-ai/glm-5.2` — small diffs only.** Its recall slides 100% → 87.5% →
  75% → 50% as the diff grows while the noise doubles. Combined with its
  breadth win, the picture is consistent: brilliant reviewer of small
  changes, unravels at scale.
- **`deepseek/deepseek-v4-pro` — precise but half-blind here too**, and
  `claude-sonnet-5` puts in its best relative showing mid-table; neither
  changes the recommendation.
- **The 0% club is two different failures.** One group found the bugs and
  drowned them: kimi-k3 hit 93.8% recall — the highest in the entire corpus —
  with 95 false positives; mistral-small posted 699. The other group went
  silent: Opus 5 returned literally nothing across all five cases, and
  `qwen3-coder-next` returned exactly one finding per case regardless of
  size. Don't read 100% precision on those rows as quality — it's absence.

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

**The calls:**

- **`nvidia/Qwen3.6-35B-A3B-NVFP4` — the local pick.** Hosted-midfield
  numbers (its F1 sits between gpt-5.4-mini and gpt-5.4-nano), 100% on the
  security lens, and the best noise discipline of any local model. Its
  weaknesses mirror the local field's generally: the softer lenses, and
  overall recall.
- **`unsloth/Qwen3.8-27B-NVFP4` — promising, unproven.** The best local F1 on
  paper, but that run used a diagnostic profile (†), and the same model's
  canonical long-horizon run collapsed to 347 false positives. Until a clean
  canonical breadth run lands, don't build on it.
- **`nvidia/Gemma-4-26B-A4B-NVFP4` — recall without judgement.** 72.9% recall
  matches GLM-5.2, but it flagged something on **every** clean change and its
  long-horizon run drowned (114 false positives). Only usable if a human
  triages everything it says.
- **`RedHatAI/gemma-4-12B-it-FP8-Dynamic` — the small-hardware option.**
  Clearly worse than the 35B on every axis, but it completes both suites on a
  12B footprint. Pick it only when the 35B doesn't fit.
- **`Qwen/Qwen3.5-9B` — too small for the job.** Good discipline (77.8% clean
  pass, 76.7% precision) but it catches 30% of the plants and went
  one-finding-per-case on long horizon. At this size the review is mostly
  reassurance.

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

**The calls:**

- **`nvidia/Qwen3.6-35B-A3B-NVFP4` confirms the pick** — modest recall, but
  the only local model whose recall *rises* as the diff grows (25% on the
  small case up to 50% on the largest), with near-zero noise and a clean run
  on the clean case. Budget real time, though: its big cases took over an
  hour each on the benchmark rig.
- **`unsloth/Qwen3.8-27B-NVFP4` is the cautionary tale.** Three runs, three
  personalities: 54.5% on a diagnostic profile, a 347-false-positive collapse
  on the canonical one, and a 57-false-positive rerun on 2.2.0. Whatever the
  right serving settings are, they haven't been pinned down publicly yet.
- **`poolside/Laguna-XS-2.1-NVFP4` and the Nemotron — not ready.** Both
  truncated on effectively every case and the Nemotron burned over a million
  output tokens per case reasoning its way to 0%. Avoid.
- **The honest summary: no local model handles huge diffs well yet.** If your
  PRs regularly run large and must stay local, expect to lean on the smallest
  capable model that fits, keep diffs small, and treat the review as a first
  pass rather than a safety net.

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
  eight bugs at the same relative positions — so recall differences come from
  size alone.

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
- every completed run — including the per-language and per-lens recall, the
  false-positive breakdowns, and the per-case size curves and wall-times the
  calls above draw on — in [`RESULTS.md`][results];
- append-only raw results under `results/raw/`; and
- commands for running the corpus against another model.

One caveat before you commit: the corpus is synthetic. It says nothing about
provider price, availability, data handling, or how a model performs on your
codebase. Shortlist a model or two here, then try them on a few recent pull
requests before setting a team-wide default.

[bench]: https://github.com/MattJColes/lgtmaybe-benchmarks
[results]: https://github.com/MattJColes/lgtmaybe-benchmarks/blob/main/RESULTS.md
[snapshot]: https://github.com/MattJColes/lgtmaybe-benchmarks/tree/7699cb6f7e00a9073b1e8d92ca0f2872370c9664
