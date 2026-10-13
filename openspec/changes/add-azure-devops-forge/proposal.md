## Why

Azure is one of the three keyless LLM providers lgtmaybe calls its wedge, but
Azure DevOps Repos, where many Azure teams keep their code, is not a supported
code host. Those teams can run the model on Azure and still have nowhere to
post the review. The `Forge` seam was built so a new host is an adapter plus an
entry in two tables, so the gap is a missing adapter rather than missing design.

## What Changes

- New `Forge.azure_devops` (`azure-devops`). `parse_pr_url` recognises
  `https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{id}` and the
  legacy `https://{org}.visualstudio.com/{project}/_git/{repo}/pullrequest/{id}`,
  carrying `organisation/project/repository` (decoded) in `PRLocator.repo`. The
  same tail on another host is refused by name as Azure DevOps Server.
- New `azure_devops/gateway.py` implementing the three-method `ReviewGateway`
  over the REST API (api-version 7.1): metadata and intent, a diff assembled from
  the latest iteration's changes and file contents at the merge base and source
  commit, each finding as a positioned thread, and the summary as an edited
  general thread carrying the shared hidden markers. Findings already posted are
  de-duplicated by their hidden ids, as on Gitea and GitLab.
- New host-neutral `core/diffbuild.py` renders two file texts as a `git diff`
  patch, so the shared parser reads the assembled diff unchanged.
- Auth: `AZURE_DEVOPS_TOKEN` (personal access token, basic auth) or the
  pipeline's `SYSTEM_ACCESSTOKEN` (bearer). `token_env_vars` lets a forge name
  more than one variable; the missing-token error names them all.
- New `azure-pipelines` command, the counterpart to `gitlab-ci`, deriving the
  pull request URL from Azure Pipelines' predefined variables.
- Docs: a how-to with an `azure-pipelines.yml`, and the host tables in the
  README and explanation pages.

## Capabilities

### New Capabilities

- `azure-devops-posting`: how an Azure DevOps pull request is located, how its
  diff is assembled, how findings and the summary are posted, how tokens are
  used, and which optional capabilities are not claimed yet.

### Modified Capabilities

- None. The `ReviewGateway` port and every other adapter are unchanged.

## Impact

- New `src/lgtmaybe/azure_devops/` and `src/lgtmaybe/core/diffbuild.py`;
  touches `core/forge.py` (enum, parser, token variables) and `cli/` (builder,
  token lookup, `azure-pipelines` command).
- No new dependency: `difflib` is standard library, HTTP is the existing httpx.
- Not built, and not claimed: incremental review, thread resolution, labels,
  commit statuses, 👎 feedback, base-branch checkout, slash commands, and Azure
  DevOps Server (on-premises).
