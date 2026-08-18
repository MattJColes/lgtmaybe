---
description: Choose a cloud or local lgtmaybe review model using measured breadth, precision, false-positive, clean-change, and long-diff results.
---

# Choose a Review Model

Cloud and local models need separate comparisons. The hosted models currently
score higher. Local models keep the code on your hardware and avoid API charges.
There are fewer published local runs, and they scored lower.

These recommendations are a snapshot from 19 August 2026. See the
[lgtmaybe benchmark repository][bench] for the live leaderboard, complete
results, raw run records, and reproduction instructions.

## Choose a Cloud Model

| Model through OpenRouter | Breadth result | Notes |
|---|---|---|
| `qwen/qwen3.8-max` | 71.4% balanced F1; 61.4% recall; 84.9% precision; 8 false positives; 77.8% clean pass | Quieter than GLM. The result is provisional: 1.9% of candidate findings await adjudication. Its current-version long-horizon run scored 71.7% with 75.0% recall. |
| `z-ai/glm-5.2` | 72.2% balanced F1; 72.9% recall; 69.6% precision; 24 false positives; 11.1% clean pass | Caught more planted issues than Qwen and reported three times as many false positives. The result is provisional. |
| `google/gemini-3.7-flash` | 62.2% balanced F1; 54.3% recall; 72.7% precision; 15 false positives; 44.4% clean pass | The only fully adjudicated result in this group. Its 81.2% long-horizon recall came from lgtmaybe 2.1.4, so it is not comparable with the 2.2.0 run. |

The main cloud comparison is Qwen against GLM. Their balanced F1 ranges overlap.
Qwen reported fewer false positives and passed more clean changes. GLM found
more planted issues. Gemini scored lower, but its breadth result is fully
adjudicated.

```bash
lgtmaybe review --provider openrouter --model qwen/qwen3.8-max
```

Replace the model ID with either of the other cloud models without changing the
provider setup. See [Review with OpenRouter](review-with-openrouter.md) for
authentication and GitHub Action examples.

## Choose a Local Model

There is only one published local run under the current lgtmaybe 2.2.0 breadth
comparison key:
**`nvidia/Qwen3.6-35B-A3B-NVFP4`** behind an OpenAI-compatible server. It scored
57.1% balanced F1 with 47.1% recall, 72.3% precision, 13 false positives, and a
44.4% clean pass rate. It trailed the hosted models above. The result is
provisional, with 2.0% of candidate findings awaiting adjudication. One result
is not enough for a local leaderboard.

The current long-horizon suite also includes
**`RedHatAI/gemma-4-12B-it-FP8-Dynamic`**, a smaller model. It scored 40.5% with
37.5% recall and 63.2% precision on lgtmaybe 2.2.0. It does not have a comparable
current breadth run, so these numbers cannot be used to rank it against Qwen.

```bash
lgtmaybe review \
  --provider openai-compatible \
  --model nvidia/Qwen3.6-35B-A3B-NVFP4 \
  --api-base http://127.0.0.1:8000/v1
```

Model weights are only part of the memory requirement; the context window and
KV cache need space too. Serving engine, quantisation, context size, and
concurrency all affect local results. See
[Run locally with ollama](run-locally-with-ollama.md) for hardware guidance, or
[Other OpenAI-compatible servers](use-a-custom-openai-compatible-endpoint.md)
for vLLM, llama.cpp, and LM Studio setup.

## The Two Benchmark Suites

The suites measure different things:

- **Breadth** uses 32 small changes across seven programming languages, GitHub
  Actions, and Terraform. It plants 72 findings across ten review lenses and
  includes nine verified-clean changes. Its balanced F1, recall, precision,
  false-positive count, and clean-pass rate describe general review quality.
- **Long horizon** uses four defect-bearing Python changes that grow from about
  3% to 90% of the input budget, plus one large clean change. Each defect case
  plants the same eight bugs. It measures whether recall survives large diffs.

Do not compare a breadth score with a long-horizon score. Do not rank runs from
different lgtmaybe versions against each other either; prompt, parsing, and
review-pipeline changes can move the result.

## What the Numbers Mean

- **Recall** is the share of planted issues the review found.
- **Precision** is the share of reported findings that matched a planted issue.
- **Balanced F1** combines breadth recall and precision; higher is better.
- **False positives** are unmatched findings. The benchmark deliberately uses
  a closed world, so a plausible issue outside the planted catalogue still
  counts as false until adjudicated.
- **Clean pass** is the share of verified-clean changes that received no
  findings.

A breadth score marked **provisional** still has unadjudicated candidates. The
cloud Qwen, GLM, and local Qwen runs cited above have 98.1%, 98.8%, and 98.0%
adjudication coverage, respectively. None of the cited runs has an immutable
audit trace, which the live leaderboard reports separately as `audit: no`.

## Source and Methodology

The figures above come from benchmark commit
[`27392b1`][snapshot]. The directly comparable breadth key is
`breadth / canonical-breadth / lgtmaybe 2.2.0`; current long-horizon results use
`long-horizon / canonical-long-horizon / lgtmaybe 2.2.0`.

The [live benchmark repository][bench] provides:

- the current leaderboard and scoring method in its README;
- every completed run, including per-language and per-lens recall, in
  [`RESULTS.md`][results];
- append-only raw results under `results/raw/`; and
- commands for running the corpus against another model.

The corpus is synthetic. It does not measure provider price, availability, data
handling, or performance on your repository. Test the shortlisted models on a
few recent pull requests before setting a team-wide default.

[bench]: https://github.com/MattJColes/lgtmaybe-benchmarks
[results]: https://github.com/MattJColes/lgtmaybe-benchmarks/blob/main/RESULTS.md
[snapshot]: https://github.com/MattJColes/lgtmaybe-benchmarks/tree/27392b1d796a86e757c33fcbe9c82505e6f0e945
