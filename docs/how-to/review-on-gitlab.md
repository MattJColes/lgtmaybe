---
description: Post lgtmaybe reviews to GitLab merge requests from GitLab CI with the gitlab-ci command - token, job setup, and how it differs from GitHub.
---

# Review Merge Requests on GitLab

lgtmaybe posts reviews to GitLab as well as GitHub. The review itself is
identical (same lenses, same reflection pass, same findings) because none of it
depends on the host. Only the adapter that fetches the diff and posts the
comments differs.

## Set It Up

1. **Create a token.** In your project, go to *Settings → Access Tokens* and
   create a project access token with the `api` scope and at least the Developer
   role. Add it under *Settings → CI/CD → Variables* as `GITLAB_TOKEN`, masked.

2. **Add a provider key.** Add the API key for whichever model you want as a
   second masked variable, for example `ANTHROPIC_API_KEY`.

3. **Add the job.** Copy
   [`examples/gitlab/.gitlab-ci.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/gitlab/.gitlab-ci.yml)
   into your repository, or merge its `lgtmaybe` job into your existing
   pipeline.

The job runs `python -m lgtmaybe gitlab-ci`, which finds the merge request from
GitLab's predefined variables (`CI_SERVER_URL` or `CI_SERVER_HOST`,
`CI_MERGE_REQUEST_PROJECT_PATH` or `CI_PROJECT_PATH`, and
`CI_MERGE_REQUEST_IID`). It must therefore run on merge request pipelines:

```yaml
rules:
  - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

On a branch pipeline there is no merge request to review, and lgtmaybe fails
with an error naming the missing `CI_MERGE_REQUEST_IID` rather than guessing.

The example job does not clone the repository (`GIT_STRATEGY: none`), so there
is no `.lgtmaybe.yml` to read. Configure the review with the command's flags:
`--provider`, `--model`, `--fallback-model`, `--api-key`, `--api-base` and
`--config`.

## Try It Locally First

Reviewing a merge request by URL happens in CI, where the token and the MR
context live. Locally, `lgtmaybe review` reviews the branch you have checked out
against the remote primary branch, and prints the findings instead of posting
them:

```bash
git switch my-feature-branch
export ANTHROPIC_API_KEY=...
lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

It runs the same review, so it is a quick way to see what a model will say about
your change before you wire up the pipeline.

## How It Differs from GitHub

GitLab has no batched review object, so each finding is posted as its own
discussion, anchored by the merge request's diff refs and the old or new file
line. The summary is a note that updates in place on each run.

Resolve-on-fix works here: GitLab discussions resolve over plain REST, where
GitHub needs GraphQL. A finding's discussion is resolved only once the follow-up
validation pass has confirmed it is fixed, never merely because the finding
stopped being reported.

The GitLab adapter also supports PR labels and the `fail_on` merge gate
(reported as a commit status). Both are config-file settings, so they need a job
that provides a file for `--config`.

These are not available:

| Not available | Why | What happens instead |
|---|---|---|
| Incremental review | Not built yet. GitLab's compare endpoint returns per-file diffs that could be reassembled, so this is "not yet", not "cannot" | Every run is a full review |
| Slash commands (`/review`, `/ask`, …) | `gitlab-ci` reviews the merge request; nothing routes MR comments to it | Re-run the pipeline to review again |
| Change overview comment | `gitlab-ci` runs the review only | No description or diagram comment is posted |
| 👎 feedback (`learn_feedback`) | Not implemented in the GitLab adapter | A downvoted finding can be posted again on a later run |
| Symbol resolution | No base-branch clone in the GitLab adapter | Reflection judges cross-file findings without the symbol's definition |
| Keyless cloud auth | The OIDC/WIF exchange is built into the GitHub composite action | Use an API key, or `ollama` |

## Fork Safety

lgtmaybe never checks out or executes merge request code. It reads the diff
through the API and treats every byte of it as untrusted input, which is why the
example job sets `GIT_STRATEGY: none` and does not clone the repository at all.
