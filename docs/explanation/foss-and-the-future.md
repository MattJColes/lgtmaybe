# FOSS and the future

lgtmaybe is MIT-licensed and provider-agnostic on purpose: the whole point is
that you own the model choice and the auth, and nothing about a review depends on
a vendor you can't swap out. That stance only matters if the project keeps
answering the questions the open-source community actually asks of it. This page
collects the ones that come up most — raised by contributors, and worth writing
down rather than re-arguing in comment threads — with where lgtmaybe stands today
and where it's likely to go.

These are **open questions, not commitments.** They're here so the direction is
visible and so a contributor can pick one up knowing the lay of the land.

## How cheap and small can the models be right now?

Cheap and small are two different axes, and lgtmaybe already leans on both: every
review runs through litellm, so the same engine drives a frontier API model, a
budget hosted model, or a model on your own GPU via `ollama` for zero per-run
cost. The honest answer is that **smaller models trade accuracy for cost**, and
the trade is real — a small model misses subtle findings and needs the
[reflection pass](what-gets-reviewed.md) turned off because it over-prunes. Our
own CI smoke test (`e2e-ollama.yml`) runs a deliberately tiny model through only
the two critical lenses with a low recall floor precisely because we don't
pretend a small model is thorough.

The floor has moved, though. As of mid-2026 the sweet spot for a *local* reviewer
is a dense ~27B model: [Qwen3.6-27B](https://simonwillison.net/2026/Apr/22/qwen36-27b/)
(Apache-2.0, April 2026) lands SWE-bench Verified at 77.2% — within a few points
of frontier API models — and runs on a single H100, an RTX 5090 at Q4, or a
16GB+ Apple Silicon laptop at lower throughput. That's the first open-weight
model that makes a genuinely local, genuinely useful review plausible without a
data-center GPU. Below that size, accuracy falls off fast enough that you're
better off either accepting narrower category coverage or routing to a hosted
model.

Practical guidance lives in the docs and the config reference, but the short
version: **pick the smallest model that clears your bar on a fixture you trust**,
and use the [eval harness](https://github.com/MattJColes/lgtmaybe/tree/main/evals)
(`python -m evals.run`) to measure parse-rate and recall before you commit to it
rather than guessing. lgtmaybe gives you the dial; it can't tell you how much
accuracy your repo is willing to give up for cost.

## Could an RLM-style multi-agent setup make it cheaper?

This is the most interesting structural question, and lgtmaybe is already part of
the way there. The engine is **not** one big prompt: it fans out per
[`ReviewCategory`](architecture.md) — security, correctness, deprecation, tests,
documentation, performance, complexity, intent — into one concurrent model call
per lens, then merges, de-dupes, and runs a self-reflection pass. That's a small
multi-agent system in everything but name: specialised reviewers, a merge step,
and a critic.

The newer idea worth tracking is
[Recursive Language Models (RLMs)](https://arxiv.org/abs/2512.24601) — treat a
long input as an *environment* the model inspects and decomposes by calling
itself over snippets, rather than stuffing the whole thing into one context
window ([reference implementation](https://github.com/alexzhang13/rlm),
[Prime Intellect write-up](https://www.primeintellect.ai/blog/rlm)). For a PR
reviewer that maps cleanly onto a real problem we already paper over with caps:
today a big diff is bounded by `max_files` and `max_input_tokens` and we review
the top-N files. An RLM-style loop could instead let a small, cheap model walk a
large diff hunk by hunk — pulling in surrounding context on demand — instead of
either truncating it or paying for a huge context on an expensive model.

Where it could make things cheaper:

- A **cheap orchestrator + cheap workers** beats one expensive long-context call
  when the diff is large, because most hunks are boring and don't need the big
  model.
- Recursion keeps each sub-call's context small, which is where token cost and
  small-model accuracy both live.

Where it could cost *more*, and why we haven't jumped: more round-trips means
more latency and more total calls, the orchestration is harder to make
deterministic (we default `temperature` to `0.0` for reproducible reviews), and
our hardening — secret redaction and prompt-injection neutralisation — currently
runs once on the way out and would need to hold on every recursive sub-call. So
the answer is **plausibly yes for large diffs, probably not worth it for small
ones**, and the right first step is an experiment behind the eval harness, not a
rewrite of the pipeline. The ports-and-adapters design exists exactly so a new
orchestration strategy can drop in without touching the providers or the GitHub
adapter.

## Can Ponytail-style "senior dev" skills be baked in?

[Ponytail](https://github.com/DietrichGebert/ponytail) — the "laziest senior dev
in the room" skill, whose whole philosophy is *the best code is the code you
never wrote* (YAGNI, reach for the standard library, question whether the thing
should exist at all) — is a good fit for a reviewer because it's a **lens, not a
workflow**. lgtmaybe already has a complexity lens that flags deep nesting,
over-long low-cohesion functions, duplicated logic, and dead code, and an intent
lens that flags out-of-scope hunks. Ponytail's instinct — "did this PR add 200
lines where 13 would do?" — is the natural extension of both.

Baking it in is low-risk because of how the prompt is built: each
[`ReviewCategory`](architecture.md) composes its own focused system prompt with
its own worked example, so adding or sharpening a "should this exist at all /
simplify-or-delete" perspective is a prompt change plus a `test_prompt.py`
assertion, not new architecture. The same goes for other senior-dev habits —
API-shape review, dependency-discipline (we already flag deprecated and
end-of-life deps), "is this the right layer for this change". The discipline the
project imposes is that **every category is asserted by a test**, so a new lens
arrives with coverage or CI rejects it.

What we'd *not* do is import a skill's full agentic workflow. Skills like Ponytail
and the broader [Superpowers](https://github.com/obra/superpowers) library are
built to steer an agent that is *writing* code; lgtmaybe only ever *reads* a diff
and never checks out or executes PR code. We can borrow the judgement, not the
build loop.

!!! tip "This is now supported"
    You don't have to wait for a built-in: **custom lenses** let you add a
    Ponytail-style "simplify or delete" lens (or any house rule) yourself, in
    config. See [Add a custom review lens](../how-to/add-a-custom-lens.md).

## How could this integrate with Superpowers / ECC / OmO / other harnesses?

There's a fast-growing ecosystem of agent harnesses and skill marketplaces —
[Superpowers](https://github.com/obra/superpowers) (obra's skills framework),
[ECC / "Everything Claude Code"](https://github.com/affaan-m/ecc) (an agent
harness with the AgentShield security layer), and
[OmO / "Oh My OpenAgent"](https://github.com/code-yeongyu/oh-my-openagent) (an
OpenCode multi-model orchestrator that routes each task to its cheapest capable
model). lgtmaybe doesn't need to *become* one of these; it's a clean, structured
**review stage** they can call. Three integration shapes, roughly in order of
effort:

1. **As a skill / slash command.** lgtmaybe already runs as a local CLI that
   reviews your `git` diff with no GitHub round-trip, and it can emit `agent`
   format — correction instructions an AI coding agent can read and apply. That
   makes it trivial to wrap as a skill in any of these harnesses: the harness
   invokes `lgtmaybe review --format agent`, feeds the findings back to its own
   agent, and the agent fixes them. This is the highest-leverage integration and
   needs essentially no new code in lgtmaybe — see
   [Fix findings with an AI agent](../how-to/fix-findings-with-an-ai-agent.md).
2. **As the review gate in a build pipeline, with the harness's own lenses.**
   Harnesses like OmO already pick a model per task by cost; lgtmaybe is the
   natural "now review what you just wrote" step, and because it's
   provider-agnostic it can review using whatever model the harness already has
   credentials for. Structured JSON output (`--json`) is the contract — and a
   harness can drop in its *own* review rules as
   [custom lens files](../how-to/add-a-custom-lens.md) (`lens_paths`) without any
   change to lgtmaybe.
3. **Consuming their model routing.** The most speculative: harnesses that route
   by cost (cheap model for bulk, expensive model for hard problems) solve the
   same problem the RLM question above raises. There's room to let lgtmaybe's
   per-category fan-out delegate model choice to an external router rather than
   using one model for every lens — security on a strong model, documentation on
   a cheap one. That would be a new credential/strategy path, so it's a larger
   change.

The throughline: lgtmaybe is **structured output over a frozen set of ports.**
Anything that can shell out to a CLI or read JSON can integrate with it today,
and anything deeper has a defined seam to build against rather than a fork.

## Want to pick one of these up?

Start with [Architecture](architecture.md) for the ports and the pipeline, then
[What gets reviewed](what-gets-reviewed.md) for the lenses. Every change here is
test-first (see [`CONTRIBUTING.md`](https://github.com/MattJColes/lgtmaybe/blob/main/CONTRIBUTING.md)) —
a prompt lens needs a `test_prompt.py` assertion, a new strategy needs a fake to
test against, and anything model-quality-shaped belongs in the eval harness, not
the pytest gate. That's what keeps these from being one-off experiments.
