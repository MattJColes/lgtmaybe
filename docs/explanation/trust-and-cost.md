---
description: How lgtmaybe's trigger gate and cost model work — who can start a review, and why opening it wide is a cost not a security choice.
---

# Trust and Cost

lgtmaybe lets you decide **who can trigger a review**. This document explains that
choice and the small cost angle behind it, so you can pick the setting that fits
your repo. The step-by-step setup is in
[Use as a GitHub Action](../how-to/use-as-github-action.md).

## Who do you want reviews to run for?

There's no single right answer — it depends on your repo and provider:

- **Everyone**, including strangers opening fork PRs — great for a welcoming
  open-source project where you want every contributor to get feedback.
- **Trusted contributors** — members and collaborators, the people who already
  have a relationship with the repo. This is the default.
- **Admins / owners only** — the tightest setting, handy while you're trying
  lgtmaybe out.

The example workflows ship with the **trusted contributors** setting, and it's a
one-line change to widen or narrow it.

## The small cost angle

The only reason this is worth a thought at all is that hosted providers bill per
run:

- **ollama is free** — it runs the model on your own hardware, so trigger it for
  whoever you like; there's no per-run cost.
- **Hosted providers** (OpenAI, Anthropic, OpenRouter, z.ai, Bedrock, Vertex,
  Azure) charge for the tokens each review uses.

So if you're on a hosted provider and your repo is public, "everyone" means
anyone can start a run. That's perfectly fine for plenty of projects — just pick
it deliberately rather than by accident. The default keeps reviews to people you
already trust, which is a sensible starting point you can open up whenever you
want.

Two built-in caps also keep any single run modest regardless of who starts it:
`max_files` (default 50) and `max_input_tokens` (default 100k). See
[What gets reviewed](what-gets-reviewed.md) for how the diff is bounded.

## How the choice is expressed

The example workflows gate the `review` job on the triggering user's
[author association](https://docs.github.com/en/graphql/reference/enums#commentauthorassociation)
— GitHub's classification of a user's relationship to the repo. By default the
job runs when that association is `OWNER`, `MEMBER`, or `COLLABORATOR`:

```yaml
if: >-
  (github.event_name == 'pull_request_target' &&
   contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
  (github.event.issue.pull_request &&
   contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
```

A maintainer can still review an outside contributor's PR any time by commenting
`/review` on it — their own association passes the gate. To change the policy,
edit the list:

- **Open it up to everyone** — drop the `if:` so any PR or `/review` comment runs
  a review.
- **Welcome returning contributors** — add `CONTRIBUTOR` to auto-review anyone
  whose PR has merged before.
- **Tighten to admins** — keep just `OWNER` (and `MEMBER` for your org).

Because the gate lives in the workflow YAML, not in the action's code, the policy
is entirely yours.

## If you want extra guardrails

Optional, for repos where you want belt-and-braces:

- Require approval for fork-PR workflow runs in
  **Settings → Actions → General → Fork pull request workflows**.
- Put the provider key behind a protected `environment`.
- Set a spending limit in your provider console.

## Reviews are safe to run for anyone

Whoever triggers a review, a malicious PR can't use it to do harm: lgtmaybe
triggers on `pull_request_target` (so it has the secrets it needs) but **never
checks out or executes PR code** — it fetches the diff through the GitHub API and
treats it as untrusted input. So opening the gate wide is a cost decision, not a
security one. The full boundary — secret redaction, prompt-injection defence, and
fork safety — is in [Data and Privacy](data-and-privacy.md).
