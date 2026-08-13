---
description: Add lgtmaybe as a GitHub Action to review every pull request automatically with inline comments and a summary.
---

# Use lgtmaybe as a GitHub Action

Use this guide to add lgtmaybe to a repository as a GitHub Actions workflow
that reviews pull requests automatically.

Use lgtmaybe from the
[GitHub Marketplace listing](https://github.com/marketplace/actions/lgtmaybe).
It is a **GitHub Action**: the reviewer runs in your workflow, and its provider,
model, and provider authentication settings live in the step's `with:` block.
GitHub Actions supplies the repository token by default, so reviews post as
`github-actions[bot]`. You can optionally install the public lgtmaybe App and
set `github_identity: lgtmaybe` to post as `lgtmaybe[bot]`; the App changes only
the GitHub author identity. The
[minimal OpenAI workflow](#minimal-workflow-openai) below shows the complete
default shape.

Ready-to-copy workflows for every cloud and API-key provider live in
[`examples/workflows/`](https://github.com/MattJColes/lgtmaybe/tree/main/examples/workflows).
`auto_diagram` is on by default, so opened and reopened pull requests receive a
concise change summary and compact Mermaid flowchart automatically. Later
`synchronize` pushes refresh it and, when the change alters a run-time flow, a
sequence diagram appears beside it.
Set it to `false` if you do
not want the extra model call.
ollama runs the model on your own machine, so it is local-only — use the
[CLI](run-locally-with-ollama.md) rather than a posting workflow.

## Contents

- [Security requirement: pull_request_target](#security-requirement-pull_request_target)
- [Who can trigger a review](#who-can-trigger-a-review)
- [Minimal workflow — openai](#minimal-workflow-openai)
- [Choose the GitHub author](#choose-the-github-author)
- [Other key-based providers](#other-key-based-providers)
- [Keyless cloud workflows](#keyless-cloud-workflows)
- [Action inputs](#action-inputs)
- [Adding a config file](#adding-a-config-file)
- [Pin to a specific version](#pin-to-a-specific-version)

## Security requirement: pull_request_target

All lgtmaybe workflows use the `pull_request_target` trigger, not
`pull_request`. This is non-negotiable:

- `pull_request_target` runs in the context of the **base branch**, so it can
  access secrets and write to the PR.
- lgtmaybe **never checks out or executes PR code** — it fetches the diff via
  the GitHub API only. The PR author cannot inject code that runs in the
  reviewer's environment.

The action derives the PR from the triggering event, so there is no `pr-url`
input to set. On an `issue_comment` event it routes the slash command
(`/review`, `/ask`, `/describe`, `/diagram`, `/improve`) to the same engine. On a
`synchronize` push the review is **incremental** by default: only the commits
added since the last completed review are re-reviewed, and earlier findings
stay open until fixed. Comment `/review full` for a full re-review on demand,
or pin the behaviour with the `incremental` input / config key.

Replies inside finding threads do not trigger lgtmaybe. A comment is not
evidence that the finding is fixed: push the fix and the next incremental review
will verify the changed code, reply `✅ Looks resolved.`, and resolve the outdated
thread when the finding has disappeared. Use `/ask` when you deliberately want
the model to answer a question.

> **Upgrading:** remove `answer_replies` from `.lgtmaybe.yml` and Action inputs,
> and remove the `pull_request_review_comment` trigger from custom workflows.
> Configuration is strict, so leaving the removed option in place is an error.

> **Note on cost.** With ollama the model runs on your own hardware, so reviews
> are free. On a hosted provider each run uses tokens you pay for, so it's worth
> a moment's thought about who can trigger one (next section) — the default keeps
> that to people you trust, and `max_files` / `max_input_tokens` keep any single
> run modest.

## Who can trigger a review

You choose who reviews run for. The example workflows gate the `review` job on
the triggering user's
[author association](https://docs.github.com/en/graphql/reference/enums#commentauthorassociation)
and default to **trusted contributors** — `OWNER`, `MEMBER`, and `COLLABORATOR`.
A maintainer can also review an outside contributor's PR any time by commenting
`/review` on it (their own association passes the gate).

The same `if:` also requires a comment to actually carry a slash command before
any job starts. `issue_comment` fires on every comment on every pull request, so
without that clause an ordinary "lgtm, merging" would claim a runner, pull the
container and boot Python only to find no command and exit — no tokens spent, but
a job queued ahead of the reviews that do have work to do. The check is
substring-based and case-insensitive, so `/REVIEW` and `/review full` both pass.

To change the policy, edit the `if:` on the `review` job:

- **Everyone** — drop the author-association checks so any PR or `/ask` /
  `/review` comment runs a review (keep the slash-command clause, or every
  comment starts a job). A friendly choice for an open project — just remember
  that on a hosted provider it means anyone can start a paid run, so pick it
  deliberately.
- **Returning contributors too** — add `CONTRIBUTOR` to auto-review anyone whose
  PR has merged before.
- **Admins only** — keep just `OWNER` (plus `MEMBER` for your org).

For extra guardrails, you can also require approval for fork-PR workflow runs in
**Settings → Actions → General → Fork pull request workflows**, or move the
provider key behind a protected `environment`. See
[Trust and Cost](../explanation/trust-and-cost.md) for the reasoning behind these
options.

## Minimal workflow — openai

```yaml
name: lgtmaybe

on:
  pull_request_target:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    # Only trusted authors (owner / member / collaborator) can trigger a review,
    # and a comment only starts a job when it carries a slash command.
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
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7 # base repo only — for .lgtmaybe.yml config
      - uses: MattJColes/lgtmaybe@v1
        with:
          provider: openai
          model: gpt-5.5
          auto_diagram: true
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

## Choose the GitHub author

The minimal workflow uses GitHub's built-in workflow token and posts as
`github-actions[bot]`. This path needs no App installation and remains the
default.

To post as `lgtmaybe[bot]`, install the public lgtmaybe App on selected
repositories, add `id-token: write` to the workflow, and set
`github_identity: lgtmaybe`. The provider, model, and provider key stay exactly
where they are. Follow [Post as lgtmaybe[bot]](./post-as-a-github-app.md) for
the complete workflow, permission boundary, and uninstall instructions.

If your organisation operates its own App, the same guide documents the
advanced `app_id` / `app_private_key` inputs.

## Other key-based providers

Swap the `provider`, `model`, and `api_key` inputs:

```yaml
# anthropic
- uses: MattJColes/lgtmaybe@v1
  with:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: ${{ secrets.ANTHROPIC_API_KEY }}

# openrouter
- uses: MattJColes/lgtmaybe@v1
  with:
    provider: openrouter
    model: anthropic/claude-sonnet-4-6
    api_key: ${{ secrets.OPENROUTER_API_KEY }}

# zai (GLM / Zhipu AI)
- uses: MattJColes/lgtmaybe@v1
  with:
    provider: zai
    model: glm-4.6
    api_key: ${{ secrets.ZAI_API_KEY }}
```

For these, the one-time setup is just: generate an API key in the provider's
console and add it as a repo secret (Settings → Secrets and variables → Actions),
then reference it as `api_key` above.

## Keyless cloud workflows

Bedrock (AWS OIDC), Vertex (GCP WIF), and Azure (Entra OIDC) need **no API keys
in secrets** — the action performs the keyless token exchange for you when you
pass `aws_role_arn`, `gcp_wif_provider`, or `azure_client_id`. All require
`id-token: write` permission. See:

- [Review with Bedrock OIDC](./review-with-bedrock-oidc.md)
- [Review with Vertex WIF](./review-with-vertex-wif.md)
- [Review with Azure OpenAI](./review-with-azure.md)

## Action inputs

| Input | Default | Description |
|---|---|---|
| `provider` | — | One of: `openai`, `openrouter`, `anthropic`, `zai`, `bedrock`, `vertex`, `azure`, `ollama`, `openai-compatible` |
| `model` | — | Model identifier for the chosen provider |
| `fallback_model` | — | Model to retry with if the primary model fails |
| `api_key` | — | API key for key-based providers (leave empty for bedrock/vertex/ollama and keyless azure) |
| `api_base` | — | Resource endpoint for azure (`https://<resource>.openai.azure.com`), or a custom base URL for other providers |
| `timeout` | provider default (ollama/openai-compatible/openrouter 1800s, cloud 600s) | Enforced wall-clock timeout for each model call. Transient failures (capacity 429s, connection blips, 5xx) are retried with exponential backoff; permanent ones (bad key, quota/billing 429, unknown model) and a call that blows this whole timeout fail fast — re-sending the identical request against the identical budget can only burn it twice |
| `temperature` | `0.0` | Sampling temperature (0.0 = deterministic) |
| `num_ctx` | `32768` | Ollama context window (ollama only; ignored for hosted providers) |
| `max_input_tokens` | `100000` | Token budget per model call before the diff is split into batches (any provider) |
| `resolve_fixed` | `true` | Auto-resolve a review conversation once its finding is fixed (set `false` to resolve manually) |
| `recursive` | `true` | Walk a file whose diff exceeds `max_input_tokens` hunk-by-hunk (RLM-style) instead of sending it whole; set `false` to disable |
| `structured_output` | `true` | Constrain output to the findings JSON schema via `response_format` (JSON mode); set `false` for an `openai-compatible` gateway that rejects it |
| `preset` | `fast` | `fast` uses four calls — security, correctness, code health, artefacts — on every provider; `full` runs one call per lens |
| `triage_model` | — | Cheap model that runs first to skip plainly-non-substantive files and rank the rest by risk; security-relevant files always escalate past triage. Unset = no triage |
| `reflect_model` | defaults to `model` | Model for the self-reflection (false-positive audit) pass — point it at a stronger model to audit a weaker reviewer's findings |
| `max_review_seconds` | `3600` | Soft wall-clock ceiling for the whole review; once passed, queued calls are skipped and partial results post with a notice (a cancelled or timed-out job does the same, via SIGINT/SIGTERM). `0` disables |
| `max_concurrency` | auto (6 cloud, 1 ollama/openai-compatible) | Concurrent review calls across the whole fan-out |
| `symbol_resolution` | `true` | During reflection, resolve a deferred finding's symbol via ast-grep in a read-only shallow clone of the base branch, so cross-file findings are re-judged against the real definition |
| `incremental` | auto | Commit-scoped incremental review on `synchronize` pushes (full review elsewhere); `true`/`false` forces it. `/review full` forces a full re-review on demand |
| `static_analysis` | `false` | Run deterministic tools (ruff, bandit, mypy, gitleaks, zizmor, ast-grep, osv-scanner, semgrep) sandboxed over the changed files: linters ground the model as untrusted hints, while gitleaks, zizmor, ast-grep and osv-scanner post directly with no model call. The image bundles these tools and an offline vulnerability database |
| `auto_describe` | `false` | Post a structured description comment when a PR is opened/reopened, before the review |
| `auto_diagram` | `true` | After each opened, reopened, or synchronized (push) PR review, post or refresh a concise change summary with a Mermaid flowchart and, when the change alters a flow, a sequence diagram; set `false` to opt out |
| `pr_labels` | `false` | Attach derived labels: `review-effort/1-5`, `possible-security-issue`, `consider-splitting` (best-effort, no extra model calls) |
| `fail_on` | — (off) | Merge-gate threshold (`info`/`low`/`medium`/`high`/`critical`). Creates a `lgtmaybe` Check Run that **fails** when any finding is at or above this severity — make it a required check to block merges. See [Gate merges on findings](#gate-merges-on-findings) |
| `profile` | `false` | Print a timing profile (per-stage and per-call tables, token and cache usage) in the Action log |
| `aws_role_arn` | — | IAM role ARN to assume via OIDC for bedrock (keyless) |
| `aws_region` | `us-east-1` | AWS region for bedrock |
| `gcp_wif_provider` | — | Workload Identity Federation provider resource name for vertex |
| `gcp_service_account` | — | GCP service account email to impersonate via WIF |
| `azure_client_id` | — | Entra (Azure AD) client ID with a federated credential — keyless azure via OIDC |
| `azure_tenant_id` | — | Entra (Azure AD) tenant ID for keyless azure |
| `config_path` | `.lgtmaybe.yml` | Path to the config file, relative to repo root |
| `github_token` | `${{ github.token }}` | Token for reading the PR and posting the review |
| `github_identity` | `actions` | GitHub author identity: `actions` posts as `github-actions[bot]`; `lgtmaybe` posts as `lgtmaybe[bot]` after the public App is installed and `id-token: write` is granted |
| `identity_broker_url` | managed endpoint | Advanced override for the public App identity exchange |
| `app_id` | — | Advanced: ID of your own GitHub App used with `app_private_key`; do not combine with `github_identity: lgtmaybe` |
| `app_private_key` | — | Advanced: private key of your own App named by `app_id` |
| `app_owner` | — | Owner for a cross-repo App token (defaults to the current repo's owner) |
| `app_repositories` | — | Repositories the App token may access, newline/comma-separated (defaults to the current repo); use with `app_owner` |
| `image` | `ghcr.io/mattjcoles/lgtmaybe:v1` | Override the container image (advanced) |

The action sets the `GITHUB_TOKEN` and provider credentials for the container
itself — you do not pass them as `env`.

## Gate merges on findings

Set `fail_on` to a severity to turn the review into a merge gate. After posting
the review, lgtmaybe creates a **Check Run** named `lgtmaybe` whose conclusion is
`failure` when any surviving finding is at or above that severity, and `success`
otherwise. Enforcement rides the Check Run — lgtmaybe never sets PR approval
state, so a clean review stays comment-only.

```yaml
- uses: MattJColes/lgtmaybe@v1
  with:
    provider: openai
    model: gpt-4o
    api_key: ${{ secrets.OPENAI_API_KEY }}
    fail_on: high   # block merge on any high/critical finding
```

To make it block merges, mark the check as required in **branch protection**:

1. Open **Settings → Branches → Branch protection rules** (or a ruleset) for the
   target branch.
2. Enable **Require status checks to pass before merging**.
3. Search for and add the **`lgtmaybe`** check. It appears in the list once the
   Action has run at least once with `fail_on` set on a PR against that branch.

A PR with a finding at or above the threshold then shows a failing `lgtmaybe`
check and cannot merge until the finding is resolved (or `fail_on` is lowered).
Leave `fail_on` unset to keep reviews advisory (the default) — no check run is
created.

Creating the Check Run requires `checks: write`. For the default Actions
identity, add it to the workflow `permissions:` block. The public
`lgtmaybe[bot]` App intentionally does not hold that permission, so
`github_identity: lgtmaybe` cannot be combined with `fail_on`; use the Actions
identity or a self-managed App granted `Checks: write`.

### What the public App cannot do

The public `lgtmaybe[bot]` App holds a deliberately minimal permission set, so
two features are unavailable under `github_identity: lgtmaybe`:

| Feature | Behaviour under the public App |
|---|---|
| `fail_on` (merge-gate Check Run) | Refused up front with a setup error — needs `checks: write` |
| `resolve_fixed` (auto-resolve fixed conversations) | `resolveReviewThread` is refused, so threads stay open. The review is unaffected: nothing is posted, the refusal is logged once with what to do, and the rest of the run continues |

Both work under the default `actions` identity (with `pull-requests: write` in
the workflow `permissions:` block) or a self-managed App holding the
corresponding permission. Set `resolve_fixed: false` if you would rather not
attempt it at all.

## Adding a config file

Place a `.lgtmaybe.yml` at the repo root to control severity thresholds, path
filters, and cost caps. See
[Configure .lgtmaybe.yml](./configure-lgtmaybe-yml.md) for all options.

## Pin to a specific version

`@v1` is a floating tag that tracks the latest `v1.x.x` release. To pin exactly,
use a full version tag:

```yaml
uses: MattJColes/lgtmaybe@v1.0.0
```
