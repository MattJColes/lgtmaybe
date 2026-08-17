---
description: The --profile table and --profile-json payload — every column, what a dash means, and the machine-readable shape to parse instead.
---

# Profile Reference

`--profile` prints a per-call breakdown of a review: where the time went, what
each lens spent, and what it produced. `--profile-json PATH` writes the same
data as JSON.

**Parse the JSON, not the table.** The table's layout is not a contract and may
be restyled; the JSON payload carries a `schema_version` precisely so it can be
pinned against.

## Which stream the table uses

The table goes to **stdout** for a human-format review, and to **stderr** when
the findings themselves are machine-readable (`--format json`, `--format agent`,
`--json`). stdout carries the deliverable, so it stays parseable — the same rule
the billable-token footer follows.

`--profile-json` writes to a **file**, so it never collides with either stream
whatever the output format is. An unwritable path is logged and skipped: a
review that produced findings is not lost to a diagnostic file.

## The table's columns

| column | meaning |
|---|---|
| `call` | the lens id, or a stage label like `reflect`, `triage`, `repair:<lens>` |
| `batch` | which batch of files the call covered |
| `tries` | requests actually issued, including adapter retries |
| `elapsed` | wall time for the call |
| `in_tok` | prompt tokens |
| `out_tok` | completion tokens |
| `think_tok` | reasoning tokens, when the route reports them |
| `think_%` | reasoning tokens as a share of the `max_tokens` ceiling |
| `cache_rd` | prompt-cache tokens read |
| `cache_wr` | prompt-cache tokens written |
| `findings` | findings parsed from this call |
| `error` | the failure reason, when the call failed |

### What `-` means

A dash is **unknown**, never zero.

`think_tok` is a dash when the route reported no reasoning breakdown. That is
deliberate: `0` would assert the model did no thinking, and nobody said that.
`think_%` is a dash when there is no ceiling to be a share of, and `findings` is
a dash for a call that does not produce review findings at all — distinct from a
call that produced none, which shows `0`.

This is the distinction that breaks naive parsers: `int(row["think_tok"])`
raises on a dash. In the JSON payload the same value is `null`.

## The JSON payload

```json
{
  "schema_version": 1,
  "wall_seconds": 12.4,
  "total_tokens": 41231,
  "returned_findings": 3,
  "stages": [{"name": "redact", "elapsed": 0.01}],
  "calls": [
    {
      "label": "security",
      "batch": 1,
      "attempts": 1,
      "elapsed": 8.2,
      "input_tokens": 36918,
      "output_tokens": 1286,
      "reasoning_tokens": null,
      "reasoning_share": null,
      "output_ceiling": null,
      "cache_read_tokens": 0,
      "cache_creation_tokens": 0,
      "findings": 2,
      "error": null
    }
  ]
}
```

Notes:

- `reasoning_tokens`, `reasoning_share`, `output_ceiling`, `findings` and
  `error` are `null` when unknown or absent — the JSON counterpart of the
  table's `-`.
- `reasoning_share` is the raw ratio the table rounds into `think_%`.
- `total_tokens` is `input + output` across all calls. Cache counters are
  deliberately excluded: routes disagree on whether a cached read is already
  inside the prompt count, so adding them would double-count on some and not
  others.
- `returned_findings` is the count after the whole pipeline, which may be lower
  than the calls' parsed total once dedupe, reflection and filtering have run.

## Stability

`schema_version` is bumped when the payload's shape changes in a way a pinned
consumer would notice. Adding a field is not such a change; removing one or
altering what it means is.
