---
description: Post lgtmaybe reviews to Gitea pull requests from Gitea Actions - token scopes, the workflow, and which GitHub features Gitea cannot serve.
---

# Review Pull Requests on Gitea

lgtmaybe posts reviews to Gitea as well as GitHub. The review itself is
identical (same lenses, same reflection pass, same findings) because none of it
depends on the host. Only the adapter that fetches the diff and posts the
comments differs.

## Set It Up

1. **Create a token.** In Gitea, go to *Settings → Applications → Access Tokens*
   and create a token with the `write:repository` and `write:issue` scopes. Add
   it to your repository as a secret named `GITEA_TOKEN`.

    You need both. `write:repository` also covers reading, and Gitea files the
    review endpoint lgtmaybe posts inline comments through
    (`POST /pulls/{index}/reviews`) and the commit-status endpoint under the
    *repository* category, not *issue*, so a token with only `read:repository`
    is refused on the one call the setup exists to make. `write:issue` covers
    the summary comment and the PR labels.

2. **Add a provider key.** Add the API key for whichever model you want as a
   second secret, for example `ANTHROPIC_API_KEY`.

3. **Add the workflow.** Copy
   [`examples/gitea/lgtmaybe.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/gitea/lgtmaybe.yml)
   into your repository at `.gitea/workflows/lgtmaybe.yml`.

That is the whole setup. Gitea Actions reimplements the GitHub Actions runtime,
so the example runs lgtmaybe's container image with the same `action` command
and `INPUT_*` variables. lgtmaybe reads `GITHUB_SERVER_URL`, which Gitea points
at your own instance, to know which host to post to.

## Try It Locally First

Reviewing a pull request by URL happens in the Actions run, where the token and
the PR context live. Locally, `lgtmaybe review` reviews the branch you have
checked out against the remote primary branch, and prints the findings instead
of posting them:

```bash
git switch my-feature-branch
export ANTHROPIC_API_KEY=...
lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

It runs the same review, so it is a quick way to see what a model will say about
your change before you wire up the workflow.

## What Works, and What Does Not

The review itself works in full: every lens, the self-reflection pass, secret
redaction, prompt-injection hardening, static-analysis fusion, path filters,
finding rules, the change overview comment, PR labels (only labels that already
exist in the repository; lgtmaybe does not create them), the `fail_on` merge gate
(as a commit status), and the `/review`, `/improve`, `/ask`, `/describe` and
`/diagram` slash commands.

These are unavailable on Gitea, and lgtmaybe skips them rather than failing:

| Not available | Why | What happens instead |
|---|---|---|
| Incremental review | Gitea's compare endpoint returns commit metadata, not a unified diff | Every run is a full review |
| Resolve-on-fix | Gitea has no thread-resolution API | Fixed findings' comments stay open |
| 👎 feedback (`learn_feedback`) | Not implemented in the Gitea adapter | A downvoted finding can be posted again on a later run |
| Symbol resolution | No base-branch clone in the Gitea adapter | Reflection judges cross-file findings without the symbol's definition |
| Keyless cloud auth | The OIDC/WIF exchange is built into the GitHub composite action | Use an API key, or `ollama` for a local model |

A submitted Gitea review cannot be edited, so lgtmaybe reads the hidden finding
IDs already on the pull request and does not post the same finding twice. The
summary lives in an ordinary comment, which is editable, so it updates in place
on each run.

## Fork Safety

Gitea has no `pull_request_target`, so the `pull_request` event runs with the
repository's secrets. The safety rule still holds for the same reason as on
GitHub: lgtmaybe never checks out or executes pull-request code. It reads the
diff through the API and treats every byte of it as untrusted input.
