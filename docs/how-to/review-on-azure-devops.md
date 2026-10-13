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

Azure Pipelines runs a pull request build from the YAML in the pull request
itself, so the job that holds the model key must not live in the repository
being reviewed. The setup below keeps it in a template in a separate, protected
repository, and the reviewed repository only extends it. Read
[Security: who can change the pipeline](#security-who-can-change-the-pipeline)
before choosing anything simpler.

1. **Create a protected template repository.** For example
   `Platform/pipeline-templates`. Add branch policies to its `main` branch so
   nobody can change it without a review from someone trusted with the secrets.
   Commit
   [`examples/azure-devops/lgtmaybe-review.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/azure-devops/lgtmaybe-review.yml)
   to it.

2. **Store the model key in a variable group.** In *Pipelines → Library*,
   create a variable group named `lgtmaybe-secrets` holding the provider key as
   a secret, for example `ANTHROPIC_API_KEY`. Use a key with a spend limit.

3. **Require the template.** On that variable group, open *Approvals and
   checks*, add a **Required template** check, and name the template
   repository, `refs/heads/main` and `lgtmaybe-review.yml`. A pipeline that does
   not extend exactly that template is refused the key, whatever its YAML says.

4. **Let the pipeline comment.** lgtmaybe posts with the job's own token,
   `System.AccessToken`. In *Project settings → Repositories → Security*,
   select the project's build service identity
   (`<Project> Build Service (<Organisation>)`) on the reviewed repository and
   allow **Contribute to pull requests**, and nothing broader.

5. **Add the pipeline to the reviewed repository.** Copy
   [`examples/azure-devops/azure-pipelines.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/azure-devops/azure-pipelines.yml),
   point its `resources.repositories` entry at your template repository, and
   create a pipeline from it. Approve its access to the template repository and
   the variable group on the first run.

6. **Run it on pull requests.** Azure Repos starts pull request builds from a
   branch policy, not from a `pr:` trigger. In *Project settings →
   Repositories → Policies*, open the target branch and add the pipeline under
   **Build validation**.

The reviewed repository's pipeline holds no steps and no secrets:

```yaml
trigger: none

resources:
  repositories:
    - repository: trusted
      type: git
      name: Platform/pipeline-templates
      ref: refs/heads/main

extends:
  template: lgtmaybe-review.yml@trusted
```

The template in the protected repository runs the GHCR image with the
`azure-pipelines` command:

```yaml
jobs:
  - job: lgtmaybe
    pool:
      vmImage: ubuntu-latest
    variables:
      - group: lgtmaybe-secrets
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
are mapped under `env:`, which is why both are listed there. The template takes
no parameters on purpose: a parameter would let the pull request's YAML pass
steps into the job that holds the secrets.

`azure-pipelines` finds the pull request from the predefined variables
`SYSTEM_COLLECTIONURI`, `SYSTEM_TEAMPROJECT`, `BUILD_REPOSITORY_NAME` and
`SYSTEM_PULLREQUEST_PULLREQUESTID`. On a build that is not validating a pull
request there is no id, and lgtmaybe fails with an error naming the missing
variable rather than guessing. A pipeline building a GitHub repository is
refused too: those pull requests live on GitHub, so use the
[GitHub Action](use-as-github-action.md). Azure Pipelines always sets
`BUILD_REPOSITORY_PROVIDER`; when it is unset (running the command by hand),
the repository is treated as Azure Repos.

Configure the review with the command's flags: `--provider`, `--model`,
`--fallback-model`, `--api-key`, `--api-base` and `--config`.

### Without Docker

On a self-hosted agent without Docker, install the CLI instead. These steps
replace the `steps:` of the protected template, never the reviewed
repository's pipeline:

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

## Security: Who Can Change the Pipeline

A pull request build in Azure Pipelines runs the YAML from the pull request's
own merge result. This is the opposite of GitHub's `pull_request_target`, where
the base branch's workflow runs. Anyone who can push a branch and open a pull
request can therefore rewrite a pipeline file in that repository, keep its
variable mappings, and send every secret those steps can read to a server of
their choosing. `checkout: none` does not help: the YAML itself is the part the
pull request controls.

So a pipeline file in the reviewed repository that maps a secret is only safe
when everyone who can open a pull request there is already trusted with that
secret. Otherwise use the setup above:

- The secret-bearing job lives in a template in a separate repository with
  branch policies, and the reviewed repository only `extends` it.
- The model key sits in a variable group guarded by a **Required template**
  check, so a pipeline that drops the `extends`, or points it at another branch
  or file, is refused the key.
- **Builds of pull requests from forks get no secrets by default. Keep that
  setting off.**
- Grant the build service only **Contribute to pull requests** on the reviewed
  repository, and keep *Limit job authorization scope to current project*
  turned on. Any pipeline can map its own `System.AccessToken`, so its
  permissions are what a rewritten pipeline could use.
- Prefer `System.AccessToken` over a personal access token. If you must use a
  personal access token, give it only **Code (Read & write)** on this
  organisation, a short expiry, and keep it in the protected variable group.
- Use a model key with a spend limit, so a leaked key has a ceiling.

lgtmaybe itself never checks out or executes pull request code. It reads the
change through the API and treats every byte of it as untrusted input.
