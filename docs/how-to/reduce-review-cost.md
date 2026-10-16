---
description: Measure what a lgtmaybe review actually spends, then cut it - triage, static analysis, prompt caching, narrower lenses, and a hard token ceiling.
---

# Reduce Review Cost

A review makes several model calls for each
[batch](../reference/glossary.md#batch) of the diff. Every call pays for its
input, even when [prompt caching](../reference/glossary.md#prompt-cache)
discounts it. Large diffs create more batches, so cost on a hosted provider can
rise quickly.

This guide shows you how to measure that cost, then lower it while keeping the
findings you need.

> Deciding **who** can trigger a review is a related but separate question. See
> [Trust and Cost](../explanation/trust-and-cost.md). Every setting below is
> documented field by field in the [configuration reference](../reference/config.md).

## Contents

- [First, Measure](#first-measure)
- [Where the Tokens Go](#where-the-tokens-go)
- [The Levers, in Order of Payoff](#the-levers-in-order-of-payoff)
- [Put a Hard Ceiling on It](#put-a-hard-ceiling-on-it)
- [If a Call Runs Past `max_tokens`](#if-a-call-runs-past-max_tokens)
- [If One Lens Floods the Review](#if-one-lens-floods-the-review)
- [What Costs More, on Purpose](#what-costs-more-on-purpose)
- [What Isn't Worth Changing](#what-isnt-worth-changing)

## First, Measure

Do not tune blind. Run once with `--profile`:

```bash
lgtmaybe review --profile
```

The table ends with the meter:

```
tokens: 158,076 billable (154,200 in / 3,876 out) across 12 calls
```

Every local review prints this line to stderr even without `--profile`, so the
meter is always in view. Redirect with `2>/dev/null` if you want it gone.

When litellm's pricing map knows the model, a second line follows:

```
estimated cost: $0.5207 (litellm pricing map)
```

It is an estimate from a price list, and that list can lag behind a brand-new
model. A missing line means "unpriced", not "free". When only some calls could
be priced, the line says how many (`; 9 of 12 calls priced`). It never passes a
partial total off as the sum. Cache reads are billed at their discount, so a
run with a warm prefix shows it.

`in` usually dwarfs `out` because every review call carries diff content. The
per-call table shows which [lens](../reference/glossary.md#lens) and batch each
call belongs to. From it you can see whether the cost comes from call count,
batch count, or one large file.

On a reasoning model, read the table's `think_tok` column next to `out_tok`. It
is how much of the output budget went on thought before the model wrote a
single finding. A summary line totals it:

```
reasoning: 41,300 of 47,900 output tokens (86%)
```

The line appears only when a route reports the breakdown. Its absence means
"not reported", not "no thinking". A high share explains a lens that ran for a
minute and returned three findings. It is the number to check before
[raising `max_tokens`](#if-a-call-runs-past-max_tokens).

In the GitHub Action, set `profile: true` and the same breakdown lands in the
job log. Every provider call also emits a structured `provider call` log line
with its own token counts, so you can total a run without the table. The
[profile reference](../reference/profile.md) lists every column.

## Where the Tokens Go

For one review:

```
input tokens  ≈  batches × calls per batch × (diff + context padding + hints)
```

- **Calls per batch.** The `fast` [preset](../reference/glossary.md#preset)
  (the default) makes five core calls per batch: security, correctness, code
  health, artefacts (tests and documentation) and architecture. Under `fast`,
  the intent check folds into the correctness call. `full` makes one call per
  built-in category, up to ten. A matching committed spec adds its own call.
  This is the biggest multiplier in the formula.
- **Batches.** A diff larger than `max_input_tokens` is split, and each batch
  pays for the full lens [fan-out](../reference/glossary.md#fan-out) again.
  The default is 100k. lgtmaybe fits it down to the model's context window
  when that is known: litellm's model map for hosted models, or `num_ctx` for
  ollama, less the output ceiling. A value you set is used as-is.
- **[Reflection](../reference/glossary.md#reflection).** One more pass over
  the findings.
- **Triage.** One extra cheap call when enabled.
- **The [change overview](../reference/glossary.md#change-overview)**
  (`auto_diagram`, on by default). It adds up to three calls per push: the
  description (`auto_describe`),
  [High Impact Areas](../reference/glossary.md#high-impact-areas)
  (`high_impact`), and the diagrams. Each section opts out on its own.
  `auto_diagram: false` drops all three.
- **Mid-review retrieval.** Off by default. When on, it adds up to one more
  call per (batch, lens). See [What Costs More, on Purpose](#what-costs-more-on-purpose).

Two settings look expensive but are not. `max_concurrency` only changes how
many calls run at once, never how many there are. `temperature` costs nothing.

## The Levers, in Order of Payoff

### 1. Turn On Triage

A cheap model reads the per-file diffs first and drops files that plainly do
not need a review. The expensive model only sees the survivors, riskiest
first.

```yaml
triage_model: claude-haiku-4-5
```

This is usually the biggest single win on a repo with lots of low-substance
churn, such as generated files, config bumps and test fixtures. Triage cannot
quietly hide a problem:

- A deterministic security floor always escalates past triage. It covers auth
  and crypto paths, migrations, IaC, CI workflows, dependency manifests,
  static-analysis hits and large [hunks](../reference/glossary.md#hunk).
- Any triage failure reviews everything.
- The summary names the skipped files.

`triage_model`, `model` and `reflect_model` share one provider and one set of
credentials.

### 2. Let Deterministic Tools Do the Deterministic Work

Findings a linter can prove do not need a language model:

```bash
pip install 'lgtmaybe[static-analysis]'
```

```yaml
static_analysis:
  enabled: true
```

With [static-analysis fusion](../reference/glossary.md#static-analysis-fusion)
on, installed tools run over the changed files at no token cost. They come in
two kinds:

- **Interpretive tools** (ruff, bandit, mypy, and semgrep with local rules)
  feed their findings to the lenses as hints to confirm or discard. This
  grounds the model, and tends to cut low-value findings as well as spend.
- **Deterministic tools** (gitleaks, zizmor, ast-grep with your own rules,
  osv-scanner) post their findings directly, with no model call.

A tool that is not installed is skipped.

### 3. Check Prompt Caching Is Working

Prompt caching is always on. When it works, it takes most of the sting out of
the lens multiplier. Lenses 2..N read the shared system preamble and diff from
cache, instead of paying full input price for them.

Check the profile's cache line:

```
cache: 412000 tokens read / 38000 created across 12 calls
```

Reads climbing across a run means caching is working. Reads stuck near zero
means you are paying full price for every lens.

lgtmaybe sets an explicit cache breakpoint on the routes that take one:

- anthropic;
- bedrock Claude and Nova;
- vertex;
- zai;
- openrouter's Claude, Gemini, GLM and MiniMax families.

OpenAI, Azure and DeepSeek cache identical prefixes server-side with no marker.
A route with neither gets no discount, which is a strong argument for picking
one that has it.

### 4. Narrow the Lenses

An explicit `categories` list runs exactly those lenses, one call each, per
batch:

```yaml
categories: [security, correctness]
```

That is two calls per batch instead of the default five. Keep the list short.
An explicit list turns off the `fast` preset's grouping, so listing six
categories costs six calls, not five.

Otherwise keep the default `fast` preset. `preset: full` doubles the call
count (up to ten calls per batch instead of five) for a modest recall gain.

### 5. Review Less

```yaml
exclude_paths: ["**/migrations/**", "**/*.generated.ts"]
max_files: 25
max_file_diff_lines: 1000
```

Generated, vendored and binary files are already skipped. A single file whose
patch exceeds `max_file_diff_lines` (default 2000) is dropped and named in the
summary. Path filters cut the rest before any model call, so they are pure
saving.

### 6. Let Re-runs Be Incremental

On the Action's `synchronize` event, a re-run is an
[incremental review](../reference/glossary.md#incremental-review). It reviews
only the commits since the last reviewed SHA, not the whole PR. That is the
default, so leave `incremental` unset. Comment `/review full` when you want
everything re-reviewed.

The `gitlab-ci` command does the same on every merge request pipeline after
the first. To re-review everything there, run the pipeline with
`LGTMAYBE_INCREMENTAL=false`.

Locally there is no [watermark](../reference/glossary.md#watermark), so every
`lgtmaybe review` re-reviews the whole branch at full price. If you are
iterating, review only what you just changed:

```bash
lgtmaybe review --uncommitted    # working-tree edits vs HEAD
```

### 7. Right-Size the Model

A smaller primary model with a stronger fallback cuts expected cost when the
primary usually finishes its calls. A healthy call never reaches the fallback.
A failed call still spends the primary model's attempts before it tries the
second model. So this does not reduce worst-case cost.

```yaml
provider: openrouter
model: google/gemini-3.7-flash
fallback_model: qwen/qwen3.8-max
```

Both models use the same configured provider and credentials. See
[`fallback_model`](configure-lgtmaybe-yml.md#fallback_model) for the recovery
order, failure behaviour, and reporting.

If cost matters more than latency, ollama runs locally for free:

```bash
lgtmaybe review --provider ollama --model qwen2.5-coder:14b
```

## Put a Hard Ceiling on It

Tuning changes the expected cost. A ceiling bounds the worst case.

```yaml
max_review_tokens: 300000
```

Billable tokens are input plus output across every model call. Once a run's
billable tokens reach the ceiling, lgtmaybe dispatches no further calls.
In-flight calls finish and their findings post. The summary says that the
budget stopped the run, and how many calls were skipped. It never turns a
failure into a silent `LGTM`.

It is off by default on purpose. Spend scales with diff size, lens count and
batch count. Any figure that protects a small repo would silently truncate a
large one's review. Read a real run's total with `--profile` first, then set
the ceiling comfortably above it. It is a runaway guard, not a tuning knob.

## If a Call Runs Past `max_tokens`

`max_tokens` caps what one call may write. A lens that hits it comes back cut
off mid-JSON (a [truncation](../reference/glossary.md#truncation)). Lowering
`max_tokens` to save money is the usual way to get here.

On `ollama`, `openai-compatible` and `openrouter`, a default ceiling of 16384
tokens per call applies, so you can get here without setting anything. The
ceiling exists because a model under structured output can fail to stop. It
keeps decoding until the timeout stops it half an hour later. The first-party
APIs get no default ceiling.

You do not have to do anything. lgtmaybe treats an over-ceiling call the same
way as one that outruns its wall clock: the batch was more than one call could
cover. So lgtmaybe halves the batch and reviews the pieces separately, each
with a fresh ceiling. It keeps whatever the model finished writing before the
cut, so nothing already paid for is thrown away. The summary says a batch was
shrunk. Because part of a lens is not a whole lens, it also says that results
may be incomplete.

That recovery costs up to twice the calls for the affected batch. The pieces
run concurrently, so on the clock it costs about one extra call. It is bounded
to one split level: a piece that still runs past the ceiling is not split
again. If the pieces still fail and you set `fallback_model`, lgtmaybe re-runs
the batch's lens once on the fallback as a last resort.

When you see splits often, the fix is usually `max_tokens`, not the diff:

```yaml
max_tokens: 32768
```

A reasoning model spends the same budget on thinking before it writes a single
finding. That is how a fifteen-line diff can truncate. The failure names the
reasoning tokens where the provider reports them. When they account for
essentially the whole ceiling, lgtmaybe does not split the batch. Smaller
pieces would each spend the same ceiling on thought. Instead, lgtmaybe re-runs
the lens:

1. once at one lower `reasoning_effort` level;
2. once with reasoning off, if that also truncates;
3. only then on `fallback_model`.

You do not have to wait for a truncation to see this coming. `--profile`
reports the same split on calls that succeeded (`think_tok` and the
`reasoning:` total). A truncated call cannot tell you that. By definition it
spent reasoning plus findings ≥ `max_tokens`, so it says nothing about the
healthy calls beside it.

Two more numbers answer the headroom question directly:

- the **`think_%`** column: that call's thinking as a share of the
  `max_tokens` ceiling it ran under;
- a **`largest reasoning share:`** line under the table, naming the single
  call closest to the ceiling and its lens.

An 8,192-token cap whose worst lens reports 50% has real headroom. One
reporting 95% is a truncation that has not happened yet. The aggregate
`reasoning:` line will not show it, because one runaway hides inside an
average.

A route that reports no breakdown shows `-` in both columns and prints no
`largest` line. That is not zero. The route never said, so there is nothing to
conclude either way.

### When the Cap Is Not the Lever

If `think_tok` is most of `out_tok`, raising `max_tokens` will not fix the
truncation, because the reasoning expands into the new headroom. Bound the
thinking instead, with
[reasoning effort](../reference/glossary.md#reasoning-effort):

```yaml
reasoning_effort: low   # none | minimal | low | medium | high | xhigh | default
```

This is measured. On lgtmaybe's own dogfood review, 5 of 9 lens calls spent
32,000-35,000 reasoning tokens before writing a single finding. That is at or
above the whole 32,768 `max_tokens` ceiling. The one large call that completed
wrote roughly 733 tokens of findings after 28,909 tokens of thought. Raising
the cap from 16k to 32k had not helped. It only gave the model more room to
think.

Unset sends nothing, so a route without a reasoning channel is unaffected.

Some models treat effort as a switch rather than a dial. GLM 5.3 through
OpenRouter spent a 12,288-token ceiling on thought at `low`, and again at
`minimal`, in 10 retries of 11. Only `none` changed it. lgtmaybe's recovery
ladder takes that step for you: one level down, then reasoning off, then the
fallback model. On such a model, configuring `none` saves the two failed calls
that come first.

#### Cheaper Is Not Better Value

Bounding thinking fixes a reasoning-bound truncation. It is not a free saving.
The direction that saves tokens is not the direction that finds bugs. Measured
on lgtmaybe's own repo, two runs per setting on the same diff:

| | `low` | `medium` |
|---|---|---|
| findings | 2, 2 | 5, 4 |
| false positives | 0, 1 | 0, 0 |
| output tokens | ~4-5k | ~14k |
| **total billable** | 180,704 | 187,191 |

`medium` roughly triples output tokens, yet costs only about 4% more in total.
A review is overwhelmingly input: the diff, the context, the system prompt.
Weighting output at 4× its input price puts it nearer 20%. Either way, nothing
like the 3× the output column suggests.

What `medium` bought was a bug in lgtmaybe's own retry backoff, which `low`
missed in both runs. `low` also produced the only
[false positive](../reference/glossary.md#false-positive) of the four runs.
Treat `reasoning_effort` as a quality dial that moves cost a little, not a cost
dial that moves quality. Turn it down when reasoning is eating your
`max_tokens` ceiling, not to save money.

#### On OpenRouter, Check the Log Line

litellm forwards `reasoning_effort` to OpenRouter only for models its
capability map already flags as reasoning-capable. The newest models are often
missing from that map. For those, lgtmaybe sends the budget in OpenRouter's own
top-level `reasoning` object, so the setting is enforced either way.

More generally, lgtmaybe names any configured parameter the resolved model
will not accept. It does this once at startup, instead of dropping the
parameter in silence:

```
configured params are not supported by this model and will be ignored
```

If that line names a setting you meant to apply, the setting is not in force.
Pick a different model or drop the setting.

`reasoning_effort: default` on OpenRouter is one real case. It is in litellm's
vocabulary but not in OpenRouter's `reasoning.effort` enum (`none`, `minimal`,
`low`, `medium`, `high`, `xhigh`). So it is reported, not quietly mapped to a
nearby level.

## If One Lens Floods the Review

A lens can stay within its token budget and still return far too many
findings. It does this by restating one claim against every line it can see.
On a benchmark diff with nothing wrong in it, a single lens returned 319 of a
review's 323 findings, each on a different line. Location dedupe does not
collapse them, because no two share a line.

So one `(batch, lens)` call contributes at most 50 findings by default. When
the bound fires, lgtmaybe keeps the highest-severity findings. The summary
names the lens and how many were dropped:

```
⚠️ Bounded a lens to the top 50 findings by severity: `intent` (269 dropped). A lens
returning many more findings than this is usually restating one claim across many
lines. Raise max_findings_per_lens to keep them all.
```

The notice usually points to a problem with the model, not a bound set too
low. A lens returning hundreds of findings is generating badly, and a higher
bound admits more of the same. Raise it only when a genuinely large diff is
losing real findings:

```yaml
max_findings_per_lens: 100   # 0 disables the bound entirely
```

An ordinary lens returns a handful of findings and never reaches the bound.

## What Costs More, on Purpose

One setting runs the other way: `mid_review_retrieval` buys findings with
tokens. Know the price before you turn it on.

```yaml
mid_review_retrieval: true   # default false
```

Normally, a lens that cannot decide without reading code outside the diff is
told to hedge the claim or drop it. So a bug whose evidence sits one file away is never
reported. With this on, the lens may instead name the files or symbols it
needs. lgtmaybe fetches them read-only and re-runs that one lens with them.

The worst case is one extra model call per (batch, lens), each also carrying
the fetched file text. On the default five-lens preset over three batches,
that is up to fifteen extra calls: the multiplier in the formula above,
doubled. In practice, only the lenses that defer pay it. The fetch is also
capped at five files, within a quarter of `max_input_tokens`. Still, budget
for the worst case.

To keep the bill in check:

- Pair it with `max_review_tokens`, so the worst case is bounded rather than
  merely unlikely. A deferral that arrives past the ceiling is skipped and
  reported.
- Fetched text rides the lens's own block, so a deferral never invalidates the
  shared cached prefix that sibling lenses read.
- Turn on `triage_model` first, so fewer files reach the lenses that might
  defer.

Measure rather than assume. Run `python -m evals.run` with and without
`--mid-review-retrieval` against your model, then compare recall to the token
total.

## What Isn't Worth Changing

**`context_lines`.** The obvious saving is not there. We measured four real
multi-file commits in this repository. The padding around each hunk costs
about 41% over sending no context at all. Yet it costs only about 17% between
`context_lines: 5` and the default `20`. Hunks with overlapping windows merge,
and the reach to the enclosing definition does most of the useful widening
either way:

| `context_lines` | 0 | 5 | 10 | 20 (default) |
| --- | --- | --- | --- | --- |
| diff payload tokens | 28,316 | 34,265 | 36,072 | 40,041 |
| vs. no context | 1.00× | 1.21× | 1.27× | 1.41× |

Saving roughly a seventh of your input tokens by hiding the function a change
sits in is a bad trade. That context keeps findings anchored and cuts false
positives. Turn the multiplier down (fewer lenses, triage, caching) before you
turn the context down.
