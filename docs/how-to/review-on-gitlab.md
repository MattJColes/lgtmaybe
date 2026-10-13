---
description: Post lgtmaybe reviews to GitLab merge requests from GitLab CI with the gitlab-ci command - token, job setup, and how it differs from GitHub.
---

# Review Merge Requests on GitLab

lgtmaybe posts reviews to GitLab as well as GitHub. The review itself is
identical (same lenses, same reflection pass, same findings) because none of it
depends on the host. Only the adapter that fetches the diff and posts the
comments differs.

## Set It Up

A merge request pipeline runs the CI configuration from the merge request's own
source branch, so where the job lives decides who can read its secrets. Read
[Security: Who Can Change the Pipeline](#security-who-can-change-the-pipeline)
before choosing where it goes.

1. **Create a token.** In your project, go to *Settings → Access Tokens* and
   create a project access token with the `api` scope, the Developer role and an
   expiry date. Add it under *Settings → CI/CD → Variables* as `GITLAB_TOKEN`,
   masked, with **Environment scope** set to `lgtmaybe`.

2. **Add a provider key.** Add the API key for whichever model you want as a
   second masked variable scoped to the `lgtmaybe` environment, for example
   `ANTHROPIC_API_KEY`. Use a key with a spend limit.

3. **Add the job.** Copy
   [`examples/gitlab/.gitlab-ci.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/gitlab/.gitlab-ci.yml)
   into your repository, or merge its `lgtmaybe` job into your existing
   pipeline. If not everyone who can push a branch is trusted with the secrets,
   put it in a separate configuration project instead, as described under
   [Keep the Pipeline Configuration Outside the Project](#keep-the-pipeline-configuration-outside-the-project).

The job names the environment `lgtmaybe` with `action: access`. That is what
lets it read the variables scoped to that environment, without recording a
deployment, while the pipeline's other jobs never receive them.

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
`--provider`, `--model`, `--fallback-model`, `--api-key`, `--api-base`,
`--config` and `--no-incremental`.

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
validation pass on an incremental run has confirmed it is fixed, never merely
because the finding stopped being reported. GitHub also requires the thread to
be marked outdated (the code under it changed). GitLab's API has no such flag,
so lgtmaybe works it out: a discussion counts as outdated when the diff since
the commit it was posted on touches the hunk around its line.

The GitLab adapter also supports PR labels and the `fail_on` merge gate
(reported as a commit status). Both are config-file settings, so they need a job
that provides a file for `--config`.

### Incremental Re-review

The first pipeline on a merge request reviews the whole change. Every later
pipeline reviews only the commits pushed since the last completed review, read
from a hidden watermark in lgtmaybe's summary note. lgtmaybe trusts that
watermark only in a note written by its own token's user, so another user's
note cannot skip a review.

It falls back to a full review whenever the increment can't be trusted:

- the branch was rebased or force-pushed (the new commits don't build on the
  last reviewed one);
- the new commits include a merge;
- GitLab no longer knows the last reviewed commit, timed out, or left part of
  the diff out because it is too large;
- any API call fails.

A failed or partial run leaves the watermark where it was, so the next run
still covers the commits it missed. Running a pipeline again on a head that was
already reviewed completely posts nothing.

GitLab has no `/review full`. To force a full re-review, run the pipeline with
the CI/CD variable `LGTMAYBE_INCREMENTAL` set to `false`, pass
`--no-incremental`, or set `incremental: false` in the config file.

### 👎 Feedback

Award a 👎 (`thumbsdown`) emoji to the first note of one of lgtmaybe's finding
discussions and later runs stop raising that finding on the merge request. Only
a 👎 from a member with the Developer role or above counts, because anyone can
award an emoji on a public project. A high or critical security finding is
never hidden this way. Set `learn_feedback: false` to turn it off.

These are not available:

| Not available | Why | What happens instead |
|---|---|---|
| Slash commands (`/review`, `/ask`, …) | `gitlab-ci` reviews the merge request; nothing routes MR comments to it | Re-run the pipeline to review again |
| Change overview comment | `gitlab-ci` runs the review only | No description or diagram comment is posted |
| Symbol resolution | No base-branch clone in the GitLab adapter | Reflection judges cross-file findings without the symbol's definition |
| Keyless cloud auth | The OIDC/WIF exchange is built into the GitHub composite action | Use an API key, or `ollama` |

## Security: Who Can Change the Pipeline

A merge request pipeline runs the `.gitlab-ci.yml` from the merge request's
source branch, not from the target branch. This is the opposite of GitHub's
`pull_request_target`, where the base branch's workflow runs, and GitLab has no
equivalent. Anyone who can push a branch to the project (the Developer role and
above) can therefore edit the `lgtmaybe` job, keep its variables, and send the
model key and `GITLAB_TOKEN` to a server of their choosing. A branch pipeline
gives them the same access without opening a merge request at all.
`GIT_STRATEGY: none` does not help: the YAML itself is the part they control.

So a job in the reviewed project's own CI configuration is only safe when
everyone who can push a branch there is already trusted with both secrets. For a
team where that holds, the setup above is enough. Otherwise, choose one of the
setups below. Each has a cost, and none of them is as simple as
`pull_request_target`.

Scoping the variables to the `lgtmaybe` environment is worth doing either way,
but it is not a defence against this. It keeps the secrets away from jobs that
never ask for them, such as a compromised test tool, and a job the merge request
controls can still name the environment.

### Merge Requests from Forks

A merge request from a fork is safe by default. Its pipeline runs in the fork,
with the fork's CI configuration and the fork's variables, and never receives
the parent project's variables. Keep it that way:

- Do not use **Run pipeline** on a fork's merge request in the parent project.
  That runs the fork's CI configuration in the parent project with all of the
  parent's variables, which hands the fork's author your secrets. GitLab shows a
  warning before it does this.
- To remove the option, disable `ci_allow_fork_pipelines_to_run_in_parent_project`
  with the [projects API](https://docs.gitlab.com/api/projects/#update-a-project).

The cost is that lgtmaybe does not review fork merge requests: their pipelines
have no key.

### Protected Variables

Marking `GITLAB_TOKEN` and the model key **Protected** keeps them out of every
pipeline on an unprotected branch, so pushing a branch no longer reveals them.
A merge request pipeline receives protected variables only when all of these
hold (the setting below arrived in GitLab 18.1):

- the project allows it, under *Settings → CI/CD → Variables → Access protected
  resources in merge request pipelines*;
- both the source and target branches are protected, and in the same project;
- the user who starts the pipeline can push or merge to the target branch.

A merge request pipeline from a fork never receives them.

The cost: lgtmaybe then reviews only merge requests between protected branches,
opened by people who could already change the pipeline there. A merge request
from an ordinary feature branch gets no token and the job fails. To skip the job
there instead, extend its rule:

```yaml
rules:
  - if: $CI_PIPELINE_SOURCE == "merge_request_event" && $CI_MERGE_REQUEST_SOURCE_BRANCH_PROTECTED == "true"
```

### Keep the Pipeline Configuration Outside the Project

This works on every GitLab tier. In the reviewed project, set *Settings → CI/CD →
General pipelines → CI/CD configuration file* to a file in a separate project,
for example `.gitlab-ci.yml@my-group/ci-config` (append `:branch-name` to pin a
branch). Every pipeline in the reviewed project, merge request and branch
pipelines alike, then runs that file, so no commit to the reviewed project can
change a job. Changing the setting needs the Maintainer role. Protect the
configuration project's default branch and give write access only to people
trusted with the secrets.

The variables stay unprotected, so merge requests from feature branches are
reviewed. What it costs:

- That file is now the project's whole pipeline, so the project's build and test
  jobs must live there too. Do not `include` the reviewed project's own CI files
  from it: a file read at the merge request's commit is the merge request's to
  edit, and its jobs could name the `lgtmaybe` environment.
- Those build and test jobs still run the merge request's code. Keep both
  variables scoped to the `lgtmaybe` environment so those jobs never receive
  them, and keep that environment out of every job but the review.
- Do not let a job that runs merge request code share a shell-executor runner
  with the review job. There, jobs run as the same user on the same host, and
  one can read another's environment.
- Keep *Settings → CI/CD → Variables → Minimum role to use pipeline variables*
  at Maintainer or above (the self-managed default), or `no_one_allowed` (the
  default for new projects on GitLab.com). A pipeline variable outranks a
  project variable, so anyone who can set one can change the review job's
  environment. `AZURE_API_BASE`, for example, tells lgtmaybe where to send the
  key when the job passes no `--api-base`.

### Pipeline Execution Policies

A [pipeline execution policy](https://docs.gitlab.com/user/application_security/policies/pipeline_execution_policies/)
(GitLab Ultimate) can add the `lgtmaybe` job to every pipeline from a separate
security policy project, so a merge request cannot change that job. On its own
it does not keep the secrets safe. Unless the policy replaces the project's
configuration entirely (`override_project_ci`), the project's own
`.gitlab-ci.yml` still runs from the merge request's branch, and its jobs
receive the same project and group variables, or can name the `lgtmaybe`
environment to get them. A policy that does replace the configuration has the
same costs as the previous section.

### Tokens and Keys

Whichever setup you choose, assume a secret can leak and limit what it is worth:

- Make `GITLAB_TOKEN` a project access token on this project only, with the
  `api` scope (posting discussions needs it; `read_api` cannot), the Developer
  role and nothing higher, and a short expiry. On GitLab.com, project access
  tokens need Premium or Ultimate. On the Free tier, use a personal access token
  of a dedicated account that is a Developer of this project alone. A leaked
  token can post as lgtmaybe, including a summary note whose watermark makes
  the next incremental run skip the commits before it.
- Use a model key with a spend limit, so a leaked key has a ceiling.

lgtmaybe itself never checks out or executes merge request code. It reads the
diff through the API and treats every byte of it as untrusted input, which is
why the example job sets `GIT_STRATEGY: none` and does not clone the repository
at all.
