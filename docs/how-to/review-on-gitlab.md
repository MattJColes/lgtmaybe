# Review merge requests on GitLab

lgtmaybe posts reviews to GitLab as well as GitHub. The review itself is
identical — same lenses, same reflection pass, same findings — because none of
that ever depended on the host. Only the adapter that fetches the diff and posts
the comments is different.

## Set it up

1. **Create a token.** In your project, go to *Settings → Access Tokens* and
   create a project access token with the `api` scope and at least the Developer
   role. Add it under *Settings → CI/CD → Variables* as `GITLAB_TOKEN`, masked.

2. **Add a provider key.** Add the API key for whichever model you want as a
   second masked variable — for example `ANTHROPIC_API_KEY`.

3. **Add the job.** Copy
   [`examples/gitlab/.gitlab-ci.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/gitlab/.gitlab-ci.yml)
   into your repository, or merge the `lgtmaybe` job into your existing
   pipeline.

The job must run on merge request pipelines — `rules: - if: $CI_PIPELINE_SOURCE
== "merge_request_event"`. On a branch pipeline there is no merge request to
review, and lgtmaybe will tell you so by name rather than guessing.

## Try it locally first

Reviewing a merge request by URL happens in CI — that is where the token and the
MR context live. Locally, `lgtmaybe review` reviews the branch you have checked
out, against the remote primary branch, and prints the findings instead of
posting them:

```bash
git switch my-feature-branch
export ANTHROPIC_API_KEY=...
lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

Same lenses, same reflection pass, same findings — so it is the quick way to see
what a model will say about your change before you wire up the pipeline.

## How it differs from GitHub

GitLab has no batched review object, so **each finding is posted as its own
discussion**, anchored by the merge request's diff refs and the old or new file
line. The summary lives in a note, which updates in place on each run.

Resolve-on-fix **does** work here: GitLab discussions resolve over plain REST,
where GitHub needs GraphQL. A finding is only resolved once the follow-up
validation pass has confirmed it is actually fixed — never merely because it
stopped being reported.

One thing is not available yet:

| Not available | Why | What happens instead |
|---|---|---|
| Incremental review | Not yet built — GitLab's compare endpoint returns per-file diffs that could be reassembled, so this is a "not yet", not a "cannot" | Every run is a full review |
| Keyless cloud auth | The OIDC/WIF exchange is a GitHub Actions feature | Use an API key, or `ollama` |

## Fork safety

lgtmaybe never checks out or executes merge request code. It reads the diff
through the API and treats every byte of it as untrusted input — which is why
the example job sets `GIT_STRATEGY: none` and does not clone the repository at
all.
