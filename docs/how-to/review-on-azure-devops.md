---
description: Post lgtmaybe reviews to Azure DevOps Repos pull requests from Azure Pipelines with the azure-pipelines command - token, pipeline setup, and how it differs from GitHub.
---

# Review Pull Requests on Azure DevOps

lgtmaybe posts reviews to Azure DevOps Repos as well as GitHub, GitLab and
Gitea. The review itself is identical (same lenses, same reflection pass, same
findings) because none of it depends on the host. Only the adapter that reads
the change and posts the comments differs.

Azure DevOps Services is supported: `dev.azure.com/{organisation}` and the
legacy `{organisation}.visualstudio.com`. Azure DevOps Server (on-premises) is
not supported yet.

## Set It Up

1. **Let the pipeline comment.** The example uses the job's own token,
   `System.AccessToken`, so there is no secret to store. In *Project settings →
   Repositories → Security*, select the project's build service identity
   (`<Project> Build Service (<Organisation>)`) and allow **Contribute to pull
   requests**.

2. **Add a provider key.** In the pipeline's *Variables*, add the API key for
   whichever model you want as a secret variable, for example
   `ANTHROPIC_API_KEY`.

3. **Add the pipeline.** Copy
   [`examples/azure-devops/azure-pipelines.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/azure-devops/azure-pipelines.yml)
   into your repository and create a pipeline from it.

4. **Run it on pull requests.** Azure Repos starts pull request builds from a
   branch policy, not from a `pr:` trigger. In *Project settings →
   Repositories → Policies*, open the target branch and add the pipeline under
   **Build validation**.

The pipeline runs the GHCR image with the `azure-pipelines` command:

```yaml
trigger: none

pool:
  vmImage: ubuntu-latest

steps:
  - checkout: none

  - script: |
      docker run --rm \
        -e SYSTEM_COLLECTIONURI -e SYSTEM_TEAMPROJECT \
        -e BUILD_REPOSITORY_NAME -e BUILD_REPOSITORY_PROVIDER \
        -e SYSTEM_PULLREQUEST_PULLREQUESTID \
        -e SYSTEM_ACCESSTOKEN -e ANTHROPIC_API_KEY \
        ghcr.io/mattjcoles/lgtmaybe:v2 \
        azure-pipelines --provider anthropic --model claude-sonnet-4-6
    displayName: lgtmaybe review
    env:
      SYSTEM_ACCESSTOKEN: $(System.AccessToken)
      ANTHROPIC_API_KEY: $(ANTHROPIC_API_KEY)
```

Secret variables, `System.AccessToken` included, reach a script only when they
are mapped under `env:`, which is why both are listed there.

`azure-pipelines` finds the pull request from the predefined variables
`SYSTEM_COLLECTIONURI`, `SYSTEM_TEAMPROJECT`, `BUILD_REPOSITORY_NAME` and
`SYSTEM_PULLREQUEST_PULLREQUESTID`. On a build that is not validating a pull
request there is no id, and lgtmaybe fails with an error naming the missing
variable rather than guessing. A pipeline building a GitHub repository is
refused too: those pull requests live on GitHub, so use the
[GitHub Action](use-as-github-action.md).

Configure the review with the command's flags: `--provider`, `--model`,
`--fallback-model`, `--api-key`, `--api-base` and `--config`.

### Without Docker

On a self-hosted agent without Docker, install the CLI instead:

```yaml
steps:
  - checkout: none
  - task: UsePythonVersion@0
    inputs:
      versionSpec: "3.12"
  - script: |
      pip install lgtmaybe
      lgtmaybe azure-pipelines --provider anthropic --model claude-sonnet-4-6
    displayName: lgtmaybe review
    env:
      SYSTEM_ACCESSTOKEN: $(System.AccessToken)
      ANTHROPIC_API_KEY: $(ANTHROPIC_API_KEY)
```

## Tokens

lgtmaybe reads the Azure DevOps token from one of two variables:

| Variable | What it is | Sent as |
|---|---|---|
| `AZURE_DEVOPS_TOKEN` | A personal access token with **Code (Read & write)** scope | Basic auth, empty user name |
| `SYSTEM_ACCESSTOKEN` | The job's own token, mapped from `$(System.AccessToken)` | Bearer token |

When both are set, `AZURE_DEVOPS_TOKEN` wins. Use a personal access token when
the comments should come from a named account rather than the build service.
Either way the token travels only in the `Authorization` header and is never
logged.

## Try It Locally First

Reviewing a pull request by URL happens in the pipeline, where the token and
the pull request live. Locally, `lgtmaybe review` reviews the branch you have
checked out against the remote primary branch, and prints the findings instead
of posting them:

```bash
git switch my-feature-branch
export ANTHROPIC_API_KEY=...
lgtmaybe review --provider anthropic --model claude-sonnet-4-6
```

## How It Differs from GitHub

Azure DevOps serves no unified diff. lgtmaybe reads the pull request's latest
iteration, lists the files it changes against the merge base, fetches each
file's text before and after, and builds the diff itself. Lockfiles, vendored
and binary files are listed as changed but never fetched, and a file side over
1 MB is treated like a binary. A pull request changing more than 300 files is
not reviewed; lgtmaybe says so instead of reviewing part of it.

Each finding is its own comment thread on the changed line, left **active** so
a "resolve all comments" branch policy can hold the merge until someone deals
with it. The summary is a general thread that is edited in place on each run.
It is created **closed**, so it never holds a merge on its own. A finding whose
thread is already open, or marked *Won't fix* or *Closed*, is not posted again;
a finding whose thread was marked *Resolved* and that comes back is.

These are not available yet:

| Not available | Why | What happens instead |
|---|---|---|
| Incremental review | Not built yet | Every run is a full review |
| Resolve-on-fix | Not built yet; Azure DevOps threads can be resolved over REST | Fixed findings' threads stay open for a human to resolve |
| Slash commands (`/review`, `/ask`, …) | `azure-pipelines` reviews the pull request; nothing routes comments to it | Queue the build again to review again |
| Change overview comment | `azure-pipelines` runs the review only | No description or diagram thread is posted |
| PR labels and the `fail_on` merge gate | Not built yet in the Azure DevOps adapter | No labels or pull request status are set |
| 👎 feedback (`learn_feedback`) | Not built yet | A downvoted finding can be posted again on a later run |
| Symbol resolution | No base-branch clone in the Azure DevOps adapter | Reflection judges cross-file findings without the symbol's definition |
| One-click suggestions | Azure DevOps has no suggestion blocks | A suggested fix shows as a code block |
| Azure DevOps Server | Only Azure DevOps Services URLs are recognised | The URL is refused with an error naming Azure DevOps Server |

Keyless model auth (Bedrock, Vertex, Azure OpenAI through GitHub OIDC) is built
into the GitHub composite action, not the `azure-pipelines` command. Use an API
key, or `ollama` against a model on the agent.

## Fork Safety

lgtmaybe never checks out or executes pull request code. It reads the change
through the API and treats every byte of it as untrusted input, which is why the
example sets `checkout: none`. Azure Pipelines does not make secrets available
to builds of pull requests from forks unless you allow it; leave that off, or
the model key and token become reachable from a fork's pipeline definition.
