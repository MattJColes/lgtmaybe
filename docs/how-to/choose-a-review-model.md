---
description: Choose a cloud or local lgtmaybe review model using measured breadth, precision, false-positive, clean-change, and long-diff results.
---

# Choose a Review Model

**Start with `google/gemini-3.8-flash` on OpenRouter.** In the 24 September
2026 benchmark on lgtmaybe 2.8.5, it led the 11 tested models on both small,
varied changes (60.9%) and large Python diffs (71.4%), and its breadth result
is fully adjudicated. It still flagged findings on seven of nine clean changes,
so check its output before acting on it.

```bash
export OPENROUTER_API_KEY=...
lgtmaybe review --provider openrouter --model google/gemini-3.8-flash
```

If large diffs matter more than small changes, consider `openai/gpt-6-luna`. It
was second on long horizon (64.6%) and finished those five cases in 440 seconds
of total review time, against Gemini's 798. Its breadth recall was much lower
(42.9% against 62.9%), so it is not the everyday default.

These recommendations come from a synthetic corpus, not your repository. Try
the candidates on recent pull requests before choosing a team default. See [Review with OpenRouter](review-with-openrouter.md) for setup.

## What the Suites Measure

- **Breadth:** 32 small changes across seven programming languages, GitHub
  Actions, and Terraform, with 72 planted findings and nine verified-clean
  changes. Each model reviews the suite three times; the table reports the
  median result.
- **Long horizon:** Four increasingly large Python changes with eight planted
  bugs each, plus one large clean change. Each model reviews the suite once.

The suites use different cases, so **compare scores within a suite, never
across suites.** Score is closed-world F0.5, which weights precision above
recall, multiplied by completeness: the share of review calls that returned
parseable findings. An unmatched finding counts as a false positive even if it
might be useful on real code. A provisional breadth score still has a few
findings awaiting human adjudication.

## Cloud Models Tested on lgtmaybe 2.8.5

### Small Changes: Breadth

| Model | Score | Recall | Precision | False positives | Clean pass |
|---|---:|---:|---:|---:|---:|
| `google/gemini-3.8-flash` | **60.9%** | 62.9% | 79.3% | 12 | 22.2% |
| `x-ai/grok-4.7` † | 55.7% | 62.9% | 70.1% | 20 | 11.1% |
| `z-ai/glm-5.3-flash` † | 55.1% | 62.9% | 70.0% | 21 | 11.1% |
| `moonshotai/kimi-k3` † | 52.0% | 65.7% | 63.3% | 27 | 0.0% |
| `openai/gpt-6-sol` | 50.0% | 60.0% | 62.9% | 26 | 11.1% |
| `openai/gpt-6-luna` † | 50.0% | 42.9% | 70.5% | 13 | 22.2% |
| `z-ai/glm-5.3` † | 49.9% | 64.3% | 67.6% | 22 | 11.1% |
| `xiaomi/mimo-v2.6-flash` † | 46.2% | 62.9% | 61.3% | 29 | 0.0% |
| `qwen/qwen3.8-flash` † | 45.4% | 62.9% | 59.5% | 32 | 11.1% |
| `openai/gpt-6-astra` | 27.7% | 44.3% | 66.7% | 16 | 44.4% |
| `anthropic/claude-opus-5.5` | 6.0% | 1.4% | 100.0% | 0 | 100.0% |

† Provisional: some borderline findings remain unadjudicated. Clean pass is
the share of nine clean changes on which the model returned no findings. Opus's
perfect precision and clean pass came from finding almost none of the planted
bugs. Astra's score was limited in part by 45.3% completeness.

### Large Diffs: Long Horizon

| Model | Score | Recall | Precision | False positives |
|---|---:|---:|---:|---:|
| `google/gemini-3.8-flash` | **71.4%** | 87.5% | 75.7% | 9 |
| `openai/gpt-6-luna` | 64.6% | 84.4% | 69.2% | 12 |
| `openai/gpt-6-sol` | 30.6% | 78.1% | 29.8% | 59 |
| `moonshotai/kimi-k3` | 30.4% | 100.0% | 37.6% | 53 |
| `qwen/qwen3.8-flash` | 28.3% | 78.1% | 58.1% | 18 |
| `openai/gpt-6-astra` | 25.5% | 56.2% | 40.0% | 27 |
| `z-ai/glm-5.3-flash` | 24.3% | 59.4% | 48.7% | 20 |
| `x-ai/grok-4.7` | 21.9% | 65.6% | 36.2% | 37 |
| `xiaomi/mimo-v2.6-flash` | 21.5% | 71.9% | 44.2% | 29 |
| `z-ai/glm-5.3` | 8.1% | 81.2% | 12.4% | 183 |
| `anthropic/claude-opus-5.5` | 0.0% | 0.0% | 100.0% | 0 |

Kimi found all 32 planted bugs but added 53 false positives. The low scores
for Qwen Flash and Xiaomi also reflect completeness below 50%: many of their
review calls produced no parseable findings. The [benchmark results][results] show
per-case recall, token use, truncation, and wall time.

## Local Models

The published local runs used older lgtmaybe versions and have not been
repeated on 2.8.5. Among local models with canonical runs on both suites,
`nvidia/Qwen3.6-35B-A3B-NVFP4` is the strongest: 48.2% breadth on 2.2.0 and
43.8% long horizon on 2.1.4. Treat those numbers as a starting point for a
local trial, not a comparison with the cloud tables above. The example below
reaches it through a local OpenAI-compatible server such as vLLM:

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model nvidia/Qwen3.6-35B-A3B-NVFP4 \
  --api-base http://127.0.0.1:8000/v1
```

See [Run locally](run-locally.md) for hardware guidance and for ollama,
llama.cpp and vLLM setup.

## Source and Limits

The [benchmark repository][bench] holds the live leaderboard, [complete
results][results], reproducible commands, and append-only raw results. Older
runs stay there for historical comparison, but their model versions, lgtmaybe
versions, and sometimes review profiles differ. The cloud recommendations on
this page use the 22 canonical runs from 24 September 2026 (11 models, two
suites).

The corpus is synthetic and does not measure provider price, availability,
data handling, or performance on your codebase. No run in this cohort has an
immutable audit trace; the live leaderboard reports audit availability.

[bench]: https://github.com/MattJColes/lgtmaybe-benchmarks
[results]: https://github.com/MattJColes/lgtmaybe-benchmarks/blob/main/RESULTS.md
