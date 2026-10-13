---
description: The --profile table and --profile-json payload - every column, the summary lines, what a dash means, and the machine-readable shape to parse instead.
---

# Profile Reference

`--profile` prints a breakdown of a review at the end of the run: where the time
went, what each model call spent, and what it produced. `--profile-json PATH`
writes the same data as JSON.

Both flags are on the local `lgtmaybe review` command. On the GitHub Action, set
the `profile: true` input to print the table in the Action log; there is no JSON
file there.

**Parse the JSON, not the table.** The table's layout is not a contract and may
change. The JSON payload carries a `schema_version` so you can pin against it.

## Which Stream the Table Uses

The table goes to **stdout** for a human-format review, and to **stderr** when
the findings themselves are machine-readable (`--format json`, `--format agent`,
`--format sarif` or `--json`), so stdout stays parseable. Without `--profile`, a local review
still prints its `tokens:` footer, always to stderr.

`--profile-json` writes to a **file**, so it never collides with either stream.
If the path can't be written, lgtmaybe logs a warning and carries on; the review
is not lost to a diagnostic file.

## Layout

```text
== lgtmaybe profile ==
total wall time: 41.3s

stage              elapsed
split                0.01s
...

call             batch tries   elapsed   in_tok  out_tok think_tok  think_% cache_rd cache_wr findings  error
security             1     1     8.20s    36918     1286       400       5%        0        0        2  -
reflect              0     1     3.10s     4000      200         -        -        0        0        -  -

cache: 0 tokens read / 0 created across 2 calls
findings: 2 parsed / 1 returned
...
tokens: 42,404 billable (40,918 in / 1,486 out) across 2 calls
```

The stage table lists each pipeline stage (`split`, `expand`, `review`,
`reflect`, `post` and so on) in the order it ran. The call table lists every
model call, slowest first.

## The Call Table's Columns

| Column | Meaning |
|---|---|
| `call` | The lens ID (`security`, `correctness`, `code-health`, `artefacts`, `spec`, a single category under `full`, or a custom lens ID), or another call: `reflect`, `triage`, `validate`, `repair:<lens>` |
| `batch` | Which batch of files the call covered; `0` for calls outside the per-batch fan-out, such as `reflect` and `triage` |
| `tries` | Requests actually issued, including adapter retries (`0` when unknown) |
| `elapsed` | Wall time for the call, including retries |
| `in_tok` | Prompt tokens |
| `out_tok` | Completion tokens, including any reasoning tokens |
| `think_tok` | Reasoning tokens, when the route reports them |
| `think_%` | Reasoning tokens as a share of the call's `max_tokens` ceiling |
| `cache_rd` | Prompt-cache tokens read |
| `cache_wr` | Prompt-cache tokens written |
| `findings` | Findings parsed from this call |
| `error` | The failure reason, when the call failed |

The change overview's calls (description, High Impact Areas, diagrams) are not
in the profile.

## Summary Lines

These lines follow the call table. A line whose data is missing is left out.

| Line | Meaning |
|---|---|
| `cache:` | Prompt-cache tokens read and written across all calls |
| `findings: N parsed / M returned` | Findings parsed from the calls, and how many survived dedupe, reflection and filtering |
| `models:` | Which models answered, when more than one did (see below) |
| `reasoning:` | Reasoning tokens as a share of output tokens, over the calls that reported a breakdown |
| `largest reasoning share:` | The single call that spent the most of its `max_tokens` ceiling on reasoning, by lens and batch |
| `tokens:` | Billable tokens (input + output) across all calls |
| `estimated cost:` | A price estimate from litellm's pricing map, shown only when it priced at least one call, and noting how many it priced |

### The `models:` Line

When every call was answered by the same model, there is no `models:` line. When
a second model answered any of them (`fallback_model`, whether the engine
escalated a truncation to it or the adapter fell back on its own), a line names
each model and how many calls it took:

```text
models: luna (7 calls), sonnet (1 call)
```

There is no per-call model column, because it would repeat one string down the
whole table. The per-call value is in the JSON payload and the structured
`provider call` log line, omitted where the adapter reports no model.

### What `-` Means

A dash means **unknown**, never zero.

`think_tok` is a dash when the route reported no reasoning breakdown, because
`0` would claim the model did no thinking. `think_%` is a dash when the call had
no ceiling to be a share of. `findings` is a dash for a call that doesn't
produce review findings at all, which is different from a call that produced
none and shows `0`.

This is what breaks naive parsers: `int(row["think_tok"])` raises on a dash. In
the JSON payload the same value is `null`.

## The JSON Payload

```json
{
  "schema_version": 1,
  "wall_seconds": 12.4,
  "total_tokens": 41231,
  "total_cost_usd": null,
  "returned_findings": 3,
  "stages": [{"name": "redact", "elapsed": 0.01}],
  "calls": [
    {
      "label": "security",
      "batch": 1,
      "elapsed": 8.2,
      "attempts": 1,
      "input_tokens": 36918,
      "output_tokens": 1286,
      "cache_read_tokens": 0,
      "cache_creation_tokens": 0,
      "error": null,
      "reasoning_tokens": null,
      "output_ceiling": null,
      "findings": 2,
      "model": "openrouter/google/gemini-3.8-flash",
      "cost_usd": null,
      "reasoning_share": null
    }
  ]
}
```

Notes:

- `reasoning_tokens`, `reasoning_share`, `output_ceiling`, `findings`, `error`,
  `model` and `cost_usd` are `null` when unknown or absent, the JSON
  counterpart of the table's `-`.
- `reasoning_share` is the raw ratio the table rounds into `think_%`.
- `total_tokens` is input + output across all calls. Cache counters are left
  out because routes disagree on whether a cached read is already inside the
  prompt count, so adding them would double-count on some routes.
- `cost_usd` and `total_cost_usd` are estimates from litellm's pricing map, and
  `null` (never `0`) when nothing was priced.
- `returned_findings` is the count after the whole pipeline, which can be lower
  than the calls' parsed total once dedupe, reflection and filtering have run.

## Stability

`schema_version` is bumped when the payload's shape changes in a way a pinned
consumer would notice. Adding a field is not such a change; removing one or
changing what it means is.
