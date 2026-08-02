---
description: Measure what a lgtmaybe review actually spends, then cut it — triage, static analysis, prompt caching, narrower lenses, and a hard token ceiling.
---

# Reduce Review Cost

A review's token cost is a **product, not a sum**: every lens re-sends the whole
diff, and every batch pays that again. On a hosted provider that multiplies into
real money faster than most people expect, and the first you usually hear about
it is the provider invoice.

This guide shows you how to see the number, then how to bring it down without
giving up the findings you actually want.

> Deciding **who** can trigger a review is a related but separate question —
> see [Trust and Cost](../explanation/trust-and-cost.md). Every setting below is
> documented field-by-field in the [configuration reference](../reference/config.md).

## Contents

- [First, measure](#first-measure)
- [Where the tokens go](#where-the-tokens-go)
- [The levers, in order of payoff](#the-levers-in-order-of-payoff)
- [Put a hard ceiling on it](#put-a-hard-ceiling-on-it)
- [If a call runs past `max_tokens`](#if-a-call-runs-past-max_tokens)
- [What costs more, on purpose](#what-costs-more-on-purpose)
- [What isn't worth changing](#what-isnt-worth-changing)

## First, measure

Do not tune blind. Run once with `--profile`:

```bash
lgtmaybe review --profile
```

The last line is the meter:

```
tokens: 158,076 billable (154,200 in / 3,876 out) across 12 calls
```

Every local review prints that same line to stderr even without `--profile`, so
the meter is always in view; redirect with `2>/dev/null` if you want it gone.

`in` dwarfing `out` is normal and is the whole story: you are paying to *send*
the diff, over and over, once per lens per batch. The per-call table above it
shows exactly which lens and which batch each call belongs to, so you can see
whether the cost is lens count, batch count, or one enormous file.

On a reasoning model, read the table's `think_tok` column next to `out_tok`.
That is how much of the output budget went on thought before the model wrote a
single finding, and a summary line totals it:

```
reasoning: 41,300 of 47,900 output tokens (86%)
```

The line appears only when a route reports the breakdown — its absence means
"not reported", not "no thinking". When the share is high, it explains a lens
that ran for a minute and returned three findings, and it is the number to
check before [raising `max_tokens`](#if-a-call-runs-past-max_tokens).

In the GitHub Action, set `profile: true` and the same breakdown lands in the
job log. Every provider call also emits a structured `provider call` log line
with its own token counts, so you can total a run without the summary.

## Where the tokens go

For one review:

```
input tokens  ≈  batches × lenses × (diff + context padding + hints)
```

- **lenses** — the `fast` preset (the default) makes **four** calls per batch;
  `full` makes one per category, up to nine. This is the biggest multiplier in
  the formula.
- **batches** — a diff larger than `max_input_tokens` (default 100k) is split,
  and *each* batch pays the full lens fan-out again.
- **reflection** — one more pass over the findings, on top.
- **triage / describe / diagram** — extra calls when enabled.
- **mid-review retrieval** — off by default; on, it adds up to one more call per
  (batch, lens). See [What costs more, on purpose](#what-costs-more-on-purpose).

Two things that look expensive but are not: `max_concurrency` only changes how
many calls run at once, never how many there are, and `temperature` costs
nothing.

## The levers, in order of payoff

### 1. Turn on triage

A cheap model reads every file first and drops the ones that plainly do not
need a review; the expensive model only sees the survivors, riskiest first.

```yaml
triage_model: claude-haiku-4-5
```

This is usually the single biggest win on a repo with lots of low-substance
churn (generated files, config bumps, test fixtures). A deterministic security
floor always escalates past triage, any triage failure reviews everything, and
skipped files are named in the summary — so it cannot quietly hide a problem.
All three model slots share one provider and one set of credentials.

### 2. Let deterministic tools do the deterministic work

Findings a linter can prove do not need a language model at all:

```bash
pip install 'lgtmaybe[static-analysis]'
```

```yaml
static_analysis:
  enabled: true
```

Installed tools (ruff, bandit, mypy, semgrep with local rules) run over the
changed files and their findings are fed to the lenses as hints to confirm or
discard. The tools cost no tokens, and grounding the model tends to reduce
low-value findings as well as spend.

### 3. Check prompt caching is actually working

`prompt_cache` is **on by default**, and when it works it takes most of the
sting out of the lens multiplier: lenses 2..N read the shared system-preamble
plus diff prefix from cache instead of paying full input price for it.

Verify it from the profile's cache line:

```
cache: 412000 tokens read / 38000 created across 12 calls
```

Reads climbing across a run means it is working. **Reads stuck near zero means
you are paying full price for every lens.** Caching needs an explicit
breakpoint on some routes (anthropic, bedrock Claude/Nova, vertex, and several
openrouter models) and identical prefixes on the rest — a route without either
gets no benefit, which is a strong argument for picking one that has it.

### 4. Narrow the lenses

Each lens you drop removes one call per batch:

```yaml
categories: [security, correctness]
```

Keep the default `fast` preset unless you have a reason not to; `preset: full`
roughly doubles the call count for a modest recall gain.

### 5. Review less

```yaml
exclude_paths: ["**/migrations/**", "**/*.generated.ts"]
max_files: 25
```

Generated, vendored and binary files are already skipped. Path filters cut the
rest before any model call is made, so they are pure saving.

### 6. Let re-runs be incremental

On the Action's `synchronize` event, a re-run reviews only the commits since the
last reviewed SHA rather than the whole PR again. That is the default — leave
`incremental` unset. Use `/review full` when you deliberately want everything
re-reviewed.

Locally there is no such watermark: **every `lgtmaybe review` re-reviews the
whole branch at full price.** If you are iterating, review only what you just
changed:

```bash
lgtmaybe review --uncommitted    # working-tree edits vs HEAD
```

### 7. Right-size the model

A smaller model on the common path with a stronger `--fallback-model` behind it
is often indistinguishable in output and much cheaper. And if cost is the
binding constraint rather than latency, ollama is free:

```bash
lgtmaybe review --provider ollama --model qwen2.5-coder:14b
```

## Put a hard ceiling on it

Tuning changes the expected cost; a ceiling bounds the worst case.

```yaml
max_review_tokens: 300000
```

Once a run's billable tokens (input + output across every model call) reach the
ceiling, no further calls are dispatched. In-flight calls finish, their findings
post, and the summary says plainly that the budget stopped the run and how many
calls were skipped. It never turns a failure into a silent `LGTM`.

It is **off by default on purpose**: spend scales with diff size, lens count and
batch count, so any figure that protects a small repo would silently truncate a
large one's review. Read a real run's total with `--profile` first, then set the
ceiling comfortably above it — it is a runaway guard, not a tuning knob.

## If a call runs past `max_tokens`

`max_tokens` caps what one call may *write*. A lens that hits it comes back cut
off mid-JSON, and lowering it to save money is the usual way to arrive here.

You do not have to do anything. lgtmaybe treats an over-ceiling call the same
way it treats one that outruns its wall clock: the batch was more than one call
could cover, so it is **halved and the pieces reviewed separately**, each with a
fresh ceiling. Whatever the model finished writing before the cut is kept as
well, so nothing already paid for is thrown away. The summary says a batch was
shrunk, and — because part of a lens is not a whole lens — the run still reports
that results may be incomplete.

The cost of that recovery is up to twice the calls for the affected batch. It is
bounded to one split level: if a piece still runs past the ceiling, it is
reported rather than split again.

When you see it happening often, the fix is usually **`max_tokens`, not the
diff**:

```yaml
max_tokens: 32768
```

A reasoning model spends the same budget on thinking before it writes a single
finding, which is how a fifteen-line diff can truncate. The failure names the
reasoning tokens where the provider reports them — if most of the ceiling went
on thought, no amount of shrinking the input will help, and the cap is what
needs raising.

You do not have to wait for a truncation to find that out. `--profile` reports
the same split on calls that **succeeded** (`think_tok`, and the `reasoning:`
total), which is the comparison a truncated call cannot give you: a call that
hit the ceiling spent reasoning + findings ≥ `max_tokens` by definition, so it
tells you nothing about the healthy calls sitting beside it.

## What costs more, on purpose

One setting in this guide runs the other way: **`mid_review_retrieval`** buys
findings with tokens rather than the reverse. Be clear-eyed about the price
before you turn it on.

```yaml
mid_review_retrieval: true   # default false
```

Normally a lens that cannot decide without reading code outside the diff is told
to hedge the claim or drop it — so a bug whose evidence sits one file away is
never reported. With this on, the lens may instead name the files (or symbols) it
needs; lgtmaybe fetches them read-only and re-runs *that one lens* with them.

The cost, worst case: **one extra model call per (batch, lens)**, each carrying
the fetched file text as well. On the default four-lens preset over three
batches, that is up to twelve extra calls — the multiplier in the formula above,
doubled. In practice only the lenses that actually defer pay it, and the fetch is
capped at five files inside a quarter of `max_input_tokens`, but budget for the
worst case rather than the average.

Ways to keep the bill honest if you want it:

- pair it with `max_review_tokens`, so the worst case is bounded rather than
  merely unlikely — a deferral arriving past the ceiling is skipped and reported;
- leave `prompt_cache` on: the fetched text rides the lens's own block, so a
  deferral never invalidates the prefix its sibling lenses read from cache;
- turn on `triage_model` first, so fewer files reach the lenses that might defer.

Measure it rather than assume: run `python -m evals.run` with and without
`--mid-review-retrieval` against your model and compare recall to the token
total.

## What isn't worth changing

**`context_lines`.** The obvious-looking saving is not there. Measured across
four real multi-file commits in this repository, the padding that surrounds each
hunk costs about **41% over sending no context at all** — but only about **17%
between `context_lines: 5` and the default `20`**, because hunks with
overlapping windows merge and the enclosing-definition reach does most of the
useful widening either way:

| `context_lines` | 0 | 5 | 10 | 20 (default) |
| --- | --- | --- | --- | --- |
| diff payload tokens | 28,316 | 34,265 | 36,072 | 40,041 |
| vs. no context | 1.00× | 1.21× | 1.27× | 1.41× |

Trading roughly a seventh of your input tokens for the model no longer seeing
the function a change sits in is a bad deal — that context is what keeps
findings anchored and cuts false positives. Turn the multiplier down (fewer
lenses, triage, caching) before you turn the context down.
