---
description: How lgtmaybe's trigger gate and cost model work - who can start a review, and why opening it wide is a cost decision, not a security one.
---

# Trust and Cost

You decide **who can trigger a review**. Hosted models charge for each run, so
that choice affects cost. The setup steps are in
[Use as a GitHub Action](../how-to/use-as-github-action.md).

## Who Do You Want Reviews to Run For?

There is no single right answer. It depends on your repo and provider:

- **Everyone**, including strangers opening fork PRs. Good for a welcoming
  open-source project where every contributor should get feedback.
- **Trusted contributors**: members and collaborators, the people who already
  have a relationship with the repo. This is the default.
- **Admins and owners only**: the tightest setting, handy while you try
  lgtmaybe out.

The example workflows ship with the trusted-contributors setting, and widening
or narrowing it is a one-line change.

## The Cost Angle

This choice matters only because hosted providers bill per run:

- **ollama** runs the model on your own hardware, so there is no per-run cost.
  Trigger it for whoever you like. The same goes for an `openai-compatible`
  server you host yourself.
- **Hosted providers** (OpenAI, Anthropic, OpenRouter, z.ai, Bedrock, Vertex,
  Azure, and hosted `openai-compatible` endpoints such as DeepSeek) charge for
  the tokens each review uses.

So on a hosted provider with a public repo, "everyone" means anyone can start a
paid run. That is fine for plenty of projects; just choose it deliberately. The
default keeps reviews to people you already trust, and you can open it up
whenever you want.

Built-in limits keep any single run bounded, whoever starts it:

- `max_files` (default 50) and `max_file_diff_lines` (default 2000) limit how
  much of the diff is reviewed. See [What Gets Reviewed](what-gets-reviewed.md).
- `max_input_tokens` (default 100k) limits how much one call sends. A larger
  diff is split into more batches, not dropped.
- `max_review_seconds` (default 3600) stops dispatching new calls once a run
  has taken an hour.
- `max_review_tokens` (off by default) stops dispatching new calls once a run
  has spent a token budget you set.

When a run hits a limit, it degrades rather than fails. A review call that
outruns its wall clock, or writes until it hits the `max_tokens` ceiling, is
not simply lost: lgtmaybe splits the batch in half, reviews the pieces, and
keeps anything the model had already written. That costs up to twice the calls
for the affected batch and is bounded to one split. A partial review always
says so: the summary carries a "results may be incomplete" notice, and a lens
that was cut short is never presented as a clean bill of health. lgtmaybe will
not answer 👍 LGTM for code no model finished reading.

To measure what a run actually spends and bring it down (triage, static
analysis, prompt caching, and the `max_review_tokens` ceiling), see
[Reduce Review Cost](../how-to/reduce-review-cost.md).

## How the Choice Is Expressed

The example workflows gate the `review` job on the triggering user's
[author association](https://docs.github.com/en/graphql/reference/enums#commentauthorassociation),
GitHub's classification of a user's relationship to the repo. By default the
job runs when that association is `OWNER`, `MEMBER`, or `COLLABORATOR`:

```yaml
if: >-
  (github.event_name == 'pull_request_target' &&
   contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
  (github.event.issue.pull_request &&
   contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association) &&
   (contains(github.event.comment.body, '/review') ||
    contains(github.event.comment.body, '/improve') ||
    contains(github.event.comment.body, '/ask') ||
    contains(github.event.comment.body, '/describe') ||
    contains(github.event.comment.body, '/diagram')))
```

A maintainer can still review an outside contributor's PR at any time by
commenting `/review` on it, because the maintainer's own association passes the
gate. To change the policy, edit the list:

- **Open it to everyone**: drop the author-association checks so any PR or
  `/review` comment runs a review.
- **Welcome returning contributors**: add `CONTRIBUTOR`, which covers anyone
  who has committed to the repo before.
- **Tighten to admins**: keep just `OWNER` (and `MEMBER` for your org).

The gate lives in your workflow YAML, not in the action's code, so the policy
is entirely yours.

The slash-command half of the comment arm saves runner time rather than
tokens. `issue_comment` fires on every comment on every pull request. Without
the check, a comment with no slash command still claims a runner, pulls the
container and boots Python before lgtmaybe exits. Filtering in the `if:` costs
nothing, and on a busy runner pool it keeps reviews from queueing behind
ordinary comment threads. The check is deliberately looser than lgtmaybe's own
parser (a case-insensitive substring match, not a prefix match), because a
guard stricter than the parser would silently disable a command that used to
work. Replies inside a finding's review thread don't trigger a run at all; a
pushed fix is checked by the next incremental review.

## If You Want Extra Guardrails

Optional, for repos that want more:

- Require approval for fork-PR workflow runs in
  **Settings → Actions → General → Fork pull request workflows**.
- Put the provider key behind a protected `environment`.
- Set a spending limit in your provider console.

## Reviews Are Safe to Run for Anyone

Whoever triggers a review, a malicious PR can't use it to do harm. On GitHub,
lgtmaybe triggers on `pull_request_target` (so it has the secrets it needs) but
never checks out or executes PR code. It fetches the diff through the host's
API and treats it as untrusted input. That second part is what does the work,
and it holds the same way on GitLab and Gitea, which have no
`pull_request_target` of their own. So opening the gate wide is a cost
decision, not a security one. The full boundary (secret redaction,
prompt-injection defence, and fork safety) is in
[Data and Privacy](data-and-privacy.md).
