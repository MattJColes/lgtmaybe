# Review pull requests on Gitea

lgtmaybe posts reviews to Gitea as well as GitHub. The review itself is
identical — same lenses, same reflection pass, same findings — because none of
that ever depended on the host. Only the adapter that fetches the diff and posts
the comments is different.

## Set it up

1. **Create a token.** In Gitea, go to *Settings → Applications → Access Tokens*
   and create a token with `write:repository` and `write:issue` scopes. Add it to
   your repository as a secret named `GITEA_TOKEN`.

    Both are needed, and `write:repository` covers reading too. Gitea scopes the
    review endpoints lgtmaybe posts inline comments through (`POST
    /pulls/{index}/reviews`) and the commit-status endpoint under the
    *repository* category, not the *issue* one — a token with only
    `read:repository` is refused on the one call the whole setup exists to make.
    `write:issue` covers the summary comment and the PR labels.

2. **Add a provider key.** Add the API key for whichever model you want as a
   second secret — for example `ANTHROPIC_API_KEY`.

3. **Add the workflow.** Copy
   [`examples/gitea/lgtmaybe.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/gitea/lgtmaybe.yml)
   into your repository at `.gitea/workflows/lgtmaybe.yml`.

That is the whole setup. Gitea Actions reimplements the GitHub Actions runtime,
so lgtmaybe's container image runs unchanged, and it reads `GITHUB_SERVER_URL`
— which Gitea points at your own instance — to know which host to post back to.

## Try it locally first

Reviewing a pull request by URL happens in the Actions run — that is where the
token and the PR context live. Locally, `lgtmaybe review` reviews the branch you
have checked out, against the remote primary branch, and prints the findings
instead of posting them:

```bash
git switch my-feature-branch
export ANTHROPIC_API_KEY=...
lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

Same lenses, same reflection pass, same findings — so it is the quick way to see
what a model will say about your change before you wire up the workflow.

## What works, and what does not

Everything about the *review* works: every lens, the self-reflection pass,
secret redaction, prompt-injection hardening, static-analysis fusion, path
filters, finding rules, and the `/review`, `/improve`, `/ask`, `/describe` and
`/diagram` slash commands.

Three things are unavailable on Gitea, and lgtmaybe skips them rather than
failing:

| Not available | Why | What happens instead |
|---|---|---|
| Incremental review | Gitea's compare endpoint returns commit metadata, not a unified diff | Every run is a full review |
| Resolve-on-fix | Gitea has no thread-resolution API | Fixed findings' comments stay open |
| Keyless cloud auth | The OIDC/WIF exchange is a GitHub Actions feature | Use an API key, or `ollama` for a local model |

Because a submitted Gitea review cannot be edited, lgtmaybe reads the hidden
finding ids already on the pull request and declines to post a finding twice.
The summary lives in an ordinary comment, which *is* editable, so it updates in
place on each run.

## Fork safety

Gitea has no `pull_request_target`, so the `pull_request` event runs with the
repository's secrets. The safety rule is unchanged and holds for the same
reason: lgtmaybe never checks out or executes pull-request code. It reads the
diff through the API and treats every byte of it as untrusted input.
