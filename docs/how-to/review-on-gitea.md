---
description: Post lgtmaybe reviews to Gitea pull requests from Gitea Actions - token scopes, the workflow, and which GitHub features Gitea cannot serve.
---

# Review Pull Requests on Gitea

lgtmaybe posts reviews to Gitea as well as GitHub. The review itself is
identical, because none of it depends on the host. It runs the same
[lenses](../reference/glossary.md#lens), the same
[reflection](../reference/glossary.md#reflection) pass, and produces the same
findings. Only the adapter that fetches the diff and posts the comments differs.

## Set It Up

The example workflow needs Gitea 1.21 or later, for the `pull_request_target`
trigger. Read
[Security: Who Can Change the Workflow](#security-who-can-change-the-workflow)
for why it matters.

1. **Create a token.** Sign in as a dedicated account with write access to the
   reviewed repository only. In Gitea, go to *Settings → Applications → Access
   Tokens*. Create a token with the `write:repository`, `write:issue` and
   `read:user` scopes (`read:user` lets lgtmaybe tell its own comments apart).
   Add it to your repository as a secret named `LGTMAYBE_GITEA_TOKEN`. Gitea
   refuses secret names that start with `GITEA_` or `GITHUB_`. The example maps
   this secret to the `GITEA_TOKEN` variable lgtmaybe reads.

    You need all three scopes:

    - `write:repository` also covers reading. Gitea files two endpoints under
      the *repository* category, not *issue*: the review endpoint lgtmaybe
      posts inline comments through (`POST /pulls/{index}/reviews`), and the
      commit-status endpoint. Resolving a fixed finding's comment is filed
      there too. So a token with only `read:repository` is refused
      on the one call the setup exists to make.
    - `write:issue` covers the summary comment and the PR labels.
    - `read:user` lets lgtmaybe ask Gitea who the token is (`GET /user`). Only
      comments that identity wrote count as lgtmaybe's. So nobody else can edit
      the summary through us, or paste a hidden id that hides a finding.
      Without it, every re-run posts a fresh summary and re-posts its findings
      rather than trusting comments it cannot attribute.

2. **Add a provider key.** Add the API key for whichever model you want as a
   second secret, for example `ANTHROPIC_API_KEY`. Use a key with a spend
   limit.

3. **Add the workflow.** Copy
   [`examples/gitea/lgtmaybe.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/gitea/lgtmaybe.yml)
   into your repository at `.gitea/workflows/lgtmaybe.yml`.

That is the whole setup. Gitea Actions reimplements the GitHub Actions runtime.
So the example runs lgtmaybe's container image with the same `action` command
and `INPUT_*` variables. lgtmaybe reads `GITHUB_SERVER_URL`, which Gitea points
at your own instance, to know which host to post to.

## Try It Locally First

Reviewing a pull request by URL happens in the Actions run, where the token and
the PR context live. Locally, `lgtmaybe review` reviews the branch you have
checked out against the remote primary branch. It prints the findings instead
of posting them:

```bash
git switch my-feature-branch
export ANTHROPIC_API_KEY=...
lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

It runs the same review. Use it to see what a model will say about your change
before you wire up the workflow.

## What Works, and What Does Not

The review itself works in full:

- every lens and the self-reflection pass;
- secret [redaction](../reference/glossary.md#redaction) and
  [prompt-injection](../reference/glossary.md#prompt-injection) hardening;
- [static-analysis fusion](../reference/glossary.md#static-analysis-fusion);
- path filters and finding rules;
- the [change overview](../reference/glossary.md#change-overview) comment;
- PR labels (only labels that already exist in the repository; lgtmaybe does
  not create them);
- the [`fail_on`](../reference/glossary.md#fail-on) merge gate (as a commit
  status);
- the `/review`, `/improve`, `/ask`, `/describe` and `/diagram` slash commands;
- resolve-on-fix, on Gitea 1.26 or later (see below).

A slash command runs only for a commenter with write access to the repository.
Gitea's workflow `if:` cannot read the commenter's role the way GitHub's
`author_association` does. So lgtmaybe checks it itself through the collaborator
permission API. Anyone else's command is ignored with a log line, exits
successfully, and makes no model call.

These are unavailable on Gitea, and lgtmaybe skips them rather than failing:

| Not available | Why | What happens instead |
|---|---|---|
| Incremental review | Gitea's compare endpoint returns commit metadata, not a unified diff | Every run is a full review |
| Resolve-on-fix before Gitea 1.26 | The resolve-comment endpoint arrived in 1.26 | Fixed findings' comments stay open |
| 👎 feedback (`learn_feedback`) | Not implemented in the Gitea adapter | A downvoted finding can be posted again on a later run |
| Symbol resolution | No base-branch clone in the Gitea adapter | Reflection judges cross-file findings without the symbol's definition |
| Keyless cloud auth | The OIDC/WIF exchange is built into the GitHub composite action | Use an API key, or `ollama` for a local model |

A submitted Gitea review cannot be edited. So lgtmaybe reads the hidden finding
IDs already on the pull request, and does not post the same finding twice. The
summary lives in an ordinary comment, which is editable, so it updates in place
on each run.

On Gitea 1.26 or later, lgtmaybe also resolves its own comment once a finding is
fixed. Gitea has no "outdated" flag, so lgtmaybe reads the file at the commit
the comment was posted on and at the new head. It resolves the comment only when
the finding is no longer reported **and** the flagged line changed. It never does
so after a run that failed or finished only partly. Gitea cannot reply in a
review thread, so no "looks resolved" reply is posted. An older server answers
the resolve call with a 404: lgtmaybe logs it once, and the comments stay open.
Set `resolve_fixed: false` to leave every comment for a person to resolve.

## Security: Who Can Change the Workflow

A `pull_request` run in Gitea Actions takes its workflow file from the pull
request's own head commit. A pull request from a branch in the same repository
runs whatever workflow it contains, with the repository's secrets. So anyone who
can push a branch could rewrite the review job. They could then send the model
key and the token to a server of their choosing.

A pull request from a fork also runs its own workflow. But Gitea passes it no
repository or organisation secrets, only the automatic job token, so lgtmaybe
cannot review it.

`pull_request_target`, available from Gitea 1.21, takes the workflow from the
base branch instead, as on GitHub. It gives that workflow the repository's
secrets for pull requests from forks too. That is why the example uses it, and
why it is the same trade-off as on GitHub. lgtmaybe never checks out or executes
pull-request code. It reads the diff through the API and treats every byte of it
as untrusted input.

The `issue_comment` arm always runs the workflow from the default branch, so a
pull request cannot change that either. Gitea still treats a comment on a fork's
pull request as a fork run, and passes it no secrets. So slash commands work
only on pull requests from branches in the repository.

What `pull_request_target` does not cover:

- **Anyone with write access can still read the secrets.** Repository and
  organisation secrets reach every workflow run in the repository. That
  includes a `push` workflow on a branch someone has just pushed, with no pull
  request involved. Give write access only to people trusted with the secrets,
  and take other contributions as pull requests from forks.
- **Gitea before 1.21 has no `pull_request_target`.** There, switch the example
  to `pull_request` only if everyone who can push a branch is trusted with the
  secrets. Pull requests from forks are not reviewed, because their runs get no
  secrets.
- **A fork's pull request starts a paid review without approval.** On an
  instance with open registration, anyone can open one. Use a model key with a
  spend limit.

Keep the token narrow too. A Gitea access token works on every repository its
owner can reach. That is why step 1 creates it on a dedicated account with
write access to the reviewed repository only. It also gives the token only the
three scopes lgtmaybe needs. Do not use `secrets.GITEA_TOKEN` in its place: that
name always resolves to the automatic job token, because Gitea refuses a secret
of your own with that name.
