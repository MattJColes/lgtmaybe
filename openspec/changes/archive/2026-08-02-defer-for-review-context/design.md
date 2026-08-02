## Context

Two pieces already exist and only need connecting:

- `engine/retrieve.py::resolve_needs` — read-only fetch of a list of paths (or
  symbols, via ast-grep), redacted, capped by token budget and file count.
- The injected `fetch_file` / `resolve_symbol` on `LLMReviewEngine`, wired in
  `cli.build_review_context` (the REST gateway's read-only file API) and in the
  local CLI (`local_file_reader` over the user's worktree).

What is missing is a caller on the *lens* side. `_review_lens` already has the
shape for it: `_complete_lens` takes an `on_wall_timeout` callback for the one
failure that says something about the payload rather than the provider. A
deferral is the same idea on the success path.

## Goals / Non-Goals

**Goals:**

- Let a lens investigate one hop instead of hedging or omitting a cross-file
  finding.
- Reuse the existing read-only, redacting fetch boundary — no second I/O path.
- Keep the shared cacheable prefix byte-identical across a batch's lenses.
- Zero change (prompt bytes, parsing, calls, cost) when the feature is off.

**Non-Goals:**

- A repository index or embedding store. The bound is one hop, not a search
  engine.
- Multi-hop investigation. Reflection already has `MAX_HOPS = 2` for rescuing a
  finding; a lens gets exactly one.
- Making it the default. See "Measurement" — the trade is unmeasured.

## Decisions

1. **The deferral rides the findings envelope**, not a separate call:
   `{"findings": [...], "needs": [...]}`. One response answers both "what did you
   find" and "what do you need", so a lens that is sure about three findings and
   unsure about a fourth reports all four in one round trip. `parse_findings`
   already ignores extra envelope keys, so the two are parsed independently.

2. **`on_needs` mirrors `on_wall_timeout`.** `_complete_lens` gains a second
   optional callback, fired after a *successful* parse when `needs` is non-empty.
   `_review_lens` supplies it only when the feature is on, a fetcher exists, and
   `batch is not None` — and `batch is None` already means "this is a retry".
   That one existing condition gives the one-hop bound for free: the re-run is
   issued with no batch, so a second `needs` is not even parsed.

3. **The fetched text goes on the lens block, never the prefix.** The prefix
   (system preamble + directory rules + hints + wrapped diff) is the cache entry
   this batch's *sibling* lenses read; appending one lens's fetched files to it
   would make every sibling miss. It sits ahead of the lens checklist inside the
   final uncached user message, so trusted instructions stay closest to the
   answer, and it is wrapped by `injection.wrap_context` with its own neutralised
   marker family — the model chose what to fetch, so a hostile diff could name
   the file carrying its payload.

4. **Merge, don't replace.** The re-run's findings are appended to the first
   call's and left to the pipeline's `_dedupe` (keyed path/line/side). Replacing
   wholesale is simpler and wrong: the lens was already confident about the first
   set, and the deferral was about something else. The cost is a duplicate pair
   when the re-run repeats itself, which is exactly what dedupe removes.

5. **The ceilings are re-checked at deferral time**, through the same
   `_skip_reason` helper the first call uses. A deferral arriving past
   `max_review_seconds` or `max_review_tokens` fetches nothing, keeps the first
   call's findings, and reports the existing incomplete-results notice — the run
   IS partial, and softening that into a clean bill of health is the one outcome
   worth more than the findings.

6. **The prompt ask is gated, and "off" is provably zero bytes.** One
   `prompt.retrieval_rules(retrieval)` returning `""` feeds the split preamble
   and all four legacy system prompts, so the ask can never reach one shape and
   miss another.

7. **Off by default.** Same posture `recursive` had before `evals/rlm` measured
   it: a weak model defers constantly, doubling the fan-out's cost, and the
   recall win is unmeasured.

## Measurement

`evals/fixtures/cross-file-recall` is the mirror image of `cross-file-fp`: there
the unshown file *refutes* a claim made from the diff alone; here it *convicts*.
The diff's `elapsed.days > refund_window(order.kind)` looks fine until you open
the unshown `payments/ledger.py` and find the window is stored in **hours** — the
window is 24× too long. Its forbidden entry keeps the other direction honest: the
same unshown files show the refund IS idempotent, so a lens that fetches them must
not claim otherwise.

A/B it against a live model (fixtures, model and sampling held fixed; only the
flag varies), reading `recall` for what the extra call buys and the profiler's
token total for what it costs:

```bash
python -m evals.run --provider openrouter --model <model> --json > off.json
python -m evals.run --provider openrouter --model <model> \
    --mid-review-retrieval --json > on.json
```

Judgement bar for flipping the default later: a clear recall gain on
`cross-file-recall` with no `clean` regression on `cross-file-fp` and the other
false-positive fixtures, at a token cost the reduce-cost guide can honestly
recommend.

## Risks / Trade-offs

- **Cost.** Worst case one extra call per (batch, lens) plus the fetched text.
  Bounded (one hop, `MAX_FETCH_FILES`, a quarter of `max_input_tokens`) and
  opt-in, and documented in `docs/how-to/reduce-review-cost.md`.
- **A model that defers to stall.** The prompt says the ask is one round and that
  withheld findings are lost; the engine ignores a second `needs` regardless.
- **Injection surface.** The fetched text is repository source — untrusted on a
  fork PR exactly like the diff — and gets identical treatment: redacted by
  `resolve_needs`, neutralised and delimiter-wrapped by `wrap_context`. Retrieval
  remains read-only; nothing is ever checked out or executed.
- **Precision.** More cross-file claims could mean more wrong ones. The
  false-positive fixtures (`cross-file-fp`, `lazy-imports`, `split-hunks`,
  `cloud-semantics`, `test-harness`) are the guard, and reflection still audits
  everything afterwards.
