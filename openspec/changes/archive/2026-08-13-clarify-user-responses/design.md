## Context

Review findings are structured as `title`, `body`, `failure_scenario`, and an
optional literal-code `suggestion`. GitHub already renders the title first and a
suggestion as a committable change. `/ask` and finding-thread replies use
separate provider prompts and return prose through their existing response
paths. See `proposal.md` for the motivation.

## Goals / Non-Goals

**Goals:**

- Shape model-generated user prose so the answer or corrective action appears first.
- Preserve the existing structured-output, security, anchoring, and posting contracts.
- Leave one deterministic test boundary for each affected prompt surface.

**Non-Goals:**

- Adding an ADHD mode, user preference, configuration field, or new response schema.
- Reformatting GitHub comments, CLI output, descriptions, diagrams, or summaries.
- Requiring time estimates or progress restatement in one-shot code-review comments.

## Decisions

### Tailor a small contract to each response surface

The review prompt will define action-first titles and causal bodies. The `/ask`
and thread-reply prompts will define answer-first prose, bounded numbered steps,
and a conditional final action. Each surface gets only the rules that fit its
job. This avoids copying the entire source skill, whose progress and time rules
do not apply to one-shot review comments.

Alternative considered: add one shared response-style constant. Rejected because
review output is structured while conversational output is prose; a shared block
would either become vague or leak irrelevant instructions between surfaces.

### Keep rendering and data models unchanged

The model already returns every field needed to produce clearer feedback, and
GitHub already exposes literal suggestions as commit actions. The change will
therefore edit prompt contracts only. This keeps JSON compatibility, custom
summary behavior, dedupe identities, localization, and downstream consumers
unchanged.

Alternative considered: add `action` or `next_step` fields and renderer labels.
Rejected because it duplicates the title/suggestion contract and expands every
provider schema for a prose-ordering problem.

### Test the deterministic prompt boundary

Acceptance tests will first assert that production-built prompts contain the
new response rules. Existing parsing, rendering, and end-to-end tests will then
verify that the unchanged output contract still works. Model adherence itself
is probabilistic and is not suitable for an exact unit assertion.

Alternative considered: assert exact generated prose from a live model.
Rejected because that would make CI nondeterministic and provider-dependent.

## Risks / Trade-offs

- [Imperative titles can sound unnatural when no concrete fix exists] → Require a plain problem statement in that case.
- [Additional prompt text consumes tokens] → Keep each surface's contract short and avoid examples beyond the existing review examples.
- [Some models may still ignore prose-ordering instructions] → Test prompt presence and rely on the existing structured examples and reflection path to constrain output.
- [Prompt wording changes can reduce cache reuse across releases] → Change only the stable shared prefix once; cache behavior within a run remains unchanged.

## Migration Plan

Ship as a backward-compatible prompt update with no migration. Roll back by
reverting the prompt text and its focused tests; stored comments, configuration,
and incremental-review markers remain valid.
