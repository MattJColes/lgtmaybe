---
description: The incidents and measurements behind lgtmaybe's recovery rules - the concurrency default, the reflection reserve, the rescue wave and the truncation ladder.
---

# Recovery Evidence

[Reliability](architecture.md#reliability-retries-timeouts-and-concurrency)
says what lgtmaybe does when a model call fails. This page records why. Each
section holds the incident or the measurement that set a rule. The code points
here, so the rule stays readable in the code and the evidence is not lost.

Every number below was measured on a real review. None of them is a setting
you need to change.

## The Concurrency Default

`max_concurrency` defaults to 6 on every provider. An extra worker cuts wall
time only when it changes how many waves of calls there are. Wall time is
`ceil(batches × lenses / workers)` call-latencies. So the pull is towards more
workers.

The [fan-out](../reference/glossary.md#fan-out) shares one API key, though.
Gateways that meter a key meter it per minute. Past some width, the burst
rate-limits itself. It did: eight concurrent calls against one OpenRouter key
gave three reviews in a row that reported "1 of 4 review calls failed" on a
429.

Two other fixes make that survivable rather than fatal. The adapter's
rate-limit backoff now outlasts a rate window, and the rescue wave retries the
failed call. Six is the same fix from the other end: a quarter less burst for a
quarter less parallelism.

Local providers used to get 1 worker. The reasoning was that a local server
handles one request at a time, so a wider pool would only queue. The queueing
is real, but the conclusion was wrong both ways:

- a server that can batch was capped at 1 for no reason;
- a server that cannot loses nothing by having work queued for it.

ollama queues up to `OLLAMA_MAX_QUEUE` (512) rather than failing. So the wall
clock is the server's throughput either way. The settings that decide it, and
their memory cost, are in
[running locally](../how-to/run-locally.md#concurrency-on-a-local-server).

There is no per-provider exception list. If one is ever needed again, keep it
as a value next to the branch that reads it. The last one was a named but empty
set two hundred lines away. It read as a policy the code no longer had.

## The Reflection Reserve

[Reflection](../reference/glossary.md#reflection) audits the findings after
the [lens](../reference/glossary.md#lens) calls. Lens calls stop early, leaving
a tenth of the token budget and a tenth of the deadline (at most 300 seconds)
for that audit. The audit also runs when the lenses overran a ceiling anyway.

Both rules come from one run. A runaway lens call used up the whole deadline,
so the audit was skipped. The review posted 325 unaudited findings. 323 of them
were false positives, on a diff with nothing wrong in it.

## The Rescue Wave

After the fan-out drains, a call that failed on the provider is re-run once.
Before this, one flaky call voided the whole round. Three reviews in a row each
reported "1 of 4 review calls failed" while the other three lenses succeeded. A
run fifteen minutes later found what the partial rounds had missed.

A split piece gets the same treatment. Before, every failed split was reported
plain, whatever the reason. So a passing provider blip inside one piece lost
the rescue the same blip would have got anywhere else. Now a piece that failed
on the provider is handed to the rescue wave, and only that piece re-runs.

## Splitting an Oversized Batch

A batch that runs out of time or out of room to answer is reviewed again in
smaller pieces. The alternative is to fail the lens, which throws away the
whole batch over a size problem. A real run did exactly that. Three of its four
lenses produced nothing, because each had more to say than one response could
hold.

## One Ladder for Both Truncation Detectors

A [truncation](../reference/glossary.md#truncation) reaches the engine by two
routes. The adapter sees it when the route reports `length`. The parser sees it
when the route reports a clean finish but the JSON body stops part-way.

The parser route used to stop with a reason saying no split was attempted. So
the same failure cost the whole lens on one model, and only a slower review on
another. One gateway model was measured at 43.2% completeness over 100k-token
inputs. What its declared ceiling bought was recovery, not accuracy. Both
routes now feed the same ladder.

The parser route's notice used to say the call hit `max_tokens`. It also
printed `output_tokens` as if that were the ceiling. Nothing proved either
claim, because the route reported a clean finish. So the notice sent readers to
raise a number that might not be the one they hit. It now says the body
stopped part-way and names no ceiling.

## The 0.9 Reasoning Share

When reasoning takes at least 0.9 of the `max_tokens` ceiling a truncation hit,
the engine does not split the batch. A smaller payload does not shrink a
thinking budget.

The two kinds of truncation sit far from 0.9 on either side. On one self-review
of this repository, five calls truncated at a 32,768 ceiling. They had spent
25,963 to 35,463 tokens reasoning. That is 0.79 to over 1.0 of the cap, mostly
0.98 or more. The calls that answered normally wrote thousands of tokens of
findings after their thinking. The failure the split exists for has the
opposite shape: little thought, then a long answer, a ratio near zero.

The plainest proof is a fifteen-line diff that truncated at the same ceiling
as a thousand-line one. Splitting there spends the whole ceiling again on every
piece and fails the same way.

The notice names both levers: lower `reasoning_effort`, or raise `max_tokens`.
The token counts cannot say which is the fix:

- thinking that grows to fill any ceiling ignores a bigger cap, so only a lower
  effort helps;
- thinking with a fixed natural size that is just over this ceiling is fixed
  outright by a bigger cap, and a lower effort costs review quality instead.

Only a re-run at a higher cap tells them apart. So the reader gets the choice,
rather than one answer stated as proven.

## Stepping the Thinking Down

Before the step-down retry, a reasoning-bound truncation was diagnosed and then
dropped. The lens was lost for that round. One review of this repository
reviewed its tests and documentation with nothing at all. Its notice named a
setting for the reader to change by hand next time.

One graded step is not always enough. On GLM 5.3 through OpenRouter (a large
PR, three rounds), `low` and `minimal` both spent the whole 12,288 ceiling on
thinking, in 10 retries of 11. The fallback model then did the same, at ten
times the price. A model whose thinking is an on or off switch does not respond
to a dial. So the next step is one call with reasoning off, before any change
of model.

## Escalation Goes Last

The adapter's own `fallback_model` used to fire the moment the primary model
truncated. So the two targeted remedies, the split and the step-down, never
ran. Every truncation cost a second model's full ceiling. Lens calls now carry
`defer_truncation`, and the engine escalates only after the targeted remedies
fail.

The summary names the model that answered, read off each result. The adapter
still falls back on its own for every failure that is not a truncation. Before,
only the escalations the engine drove were disclosed. Half the rescues were
named and the other half were silent.

## Re-asking Without the Schema

A reply can arrive whole and well-formed and still not be findings. The engine
then re-runs the lens once without the provider's JSON schema. The prompt asks
for the findings JSON either way, and the parser is lenient. So a model whose
constrained decoding produced something unusable may answer well without it.
Two Claude models did exactly this through OpenRouter. With the schema on,
they lost every lens in one run.

## Diagnosing Unparseable Replies

At DEBUG, the log keeps the first 2,000 and last 500 characters of an
unparseable reply. The failures seen so far ran 676 to 1,201 tokens, roughly
2.7 to 4.8 KB. So the excerpt holds most of them whole.

Each failure also logs a short digest of the reply. The benchmark run behind
this logging recorded that one case failed in all three repeats. It could not
say whether the three replies were the same. The digest answers that across
runs and machines, without logging any of the reply.
