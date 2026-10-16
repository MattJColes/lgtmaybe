---
description: Add lgtmaybe as a GitHub Action to review every pull request automatically with inline comments and a summary.
---

# Use lgtmaybe as a GitHub Action

This guide adds lgtmaybe to a repository as a GitHub Actions workflow. The
workflow reviews every pull request (PR) automatically.

!!! tip "Not on GitHub?"

    lgtmaybe runs the same review on GitLab and Gitea. See
    [Review on GitLab](./review-on-gitlab.md) or
    [Review on Gitea](./review-on-gitea.md).

lgtmaybe is listed on the
[GitHub Marketplace](https://github.com/marketplace/actions/lgtmaybe). It is a
**GitHub Action**: the reviewer runs in your workflow. Its
[provider](../reference/glossary.md#provider), model and provider credentials go in the step's
`with:` block.

Reviews post as `github-actions[bot]` using the workflow's own token. To post
as `lgtmaybe[bot]` instead, install the public lgtmaybe App and set
`github_identity: lgtmaybe`. The App changes only the comment author.

The [minimal OpenAI workflow](#minimal-workflow-openai) below shows the complete
default setup.
[`examples/workflows/`](https://github.com/MattJColes/lgtmaybe/tree/main/examples/workflows)
has a ready-to-copy workflow for every cloud and API-key provider.

Alongside the review, lgtmaybe posts a
[change overview](../reference/glossary.md#change-overview) comment on opened, reopened and
pushed-to pull requests (`auto_diagram`, on by default). It holds:

- a description;
- a [risk-of-change](../reference/glossary.md#risk-of-change) rating;
- a **[High Impact Areas](../reference/glossary.md#high-impact-areas)** section;
- a Mermaid flowchart, plus a sequence diagram when the change alters a
  run-time flow.

Each push refreshes it. Set `auto_diagram: false` for no overview. To drop one
section, set `auto_describe: false`, `high_impact: false` or `risk: false`.

lgtmaybe skips the automatic overview when a Mermaid diagram is already there,
in the PR description or in a comment lgtmaybe didn't post. The review summary
says so. `/diagram` still posts one on request.

ollama runs the model on your own machine, so use it through the
[CLI](run-locally-with-ollama.md) rather than a posting workflow.

## Contents

- [Security requirement: pull_request_target](#security-requirement-pull_request_target)
- [Who can trigger a review](#who-can-trigger-a-review)
- [Minimal workflow: openai](#minimal-workflow-openai)
- [Choose the GitHub author](#choose-the-github-author)
- [Other key-based providers](#other-key-based-providers)
- [Keyless cloud workflows](#keyless-cloud-workflows)
- [Action inputs](#action-inputs)
- [Gate merges on findings](#gate-merges-on-findings)
- [Adding a config file](#adding-a-config-file)
- [Pin to a specific version](#pin-to-a-specific-version)
- [On another code host](#on-another-code-host)

## Security Requirement: pull_request_target

All lgtmaybe workflows use the `pull_request_target` trigger, not
`pull_request`. This is non-negotiable:

- `pull_request_target` runs in the context of the **base branch** (the
  branch the PR merges into), so it can read secrets and write to the PR.
- lgtmaybe **never checks out or runs PR code**. It fetches the
  [diff](../reference/glossary.md#diff) through the GitHub API, so the PR author cannot get code to
  run in the reviewer's environment.

The action reads the PR from the triggering event, so there is no PR URL input
to set. On an `issue_comment` event it runs the slash command (`/review`,
`/improve`, `/ask`, `/describe`, `/diagram`) through the same engine.

On a `synchronize` push (new commits on the PR), the review is
**[incremental](../reference/glossary.md#incremental-review)** by default. lgtmaybe reviews only the
commits added since the last completed review. Earlier
[findings](../reference/glossary.md#finding) stay open until fixed. Comment `/review full` for a
full re-review, or pin the behaviour with the `incremental` input or config key.

Replies inside finding threads do not trigger lgtmaybe. A reply is not evidence
that a finding is fixed, so push the fix instead. lgtmaybe resolves the thread
and replies `✅ Looks resolved.` once both of these are true:

- the next review no longer reports the finding;
- GitHub marks the thread outdated.

Use `/ask` when you want the model to answer a question.

> **Upgrading:** lgtmaybe no longer has the `answer_replies` option. Delete it
> from `.lgtmaybe.yml` (configuration is strict, so a leftover key is an error)
> and from your Action inputs. Also remove the `pull_request_review_comment`
> trigger from custom workflows.

> **Note on cost.** With ollama the model runs on your own hardware, so reviews
> are free. On a hosted provider each run uses tokens you pay for, so decide
> who can trigger one (next section). The default limits that to people you
> trust. `max_files` (in `.lgtmaybe.yml`) and `max_input_tokens` keep any single
> run modest.

## Who Can Trigger a Review

You choose who reviews run for. The example workflows gate the `review` job on
the triggering user's
[author association](https://docs.github.com/en/graphql/reference/enums#commentauthorassociation):
GitHub's label for how that user relates to the repository. They default to
**trusted contributors**: `OWNER`, `MEMBER`, and `COLLABORATOR`.

A maintainer can still review an outside contributor's PR at any time by
commenting `/review` on it. The maintainer's own association passes the gate.

The same `if:` also requires a comment to carry a slash command before any job
starts. `issue_comment` fires on every comment on every pull request. Without
that clause, an ordinary "lgtm, merging" would claim a runner, pull the
container and start Python, only to find no command and exit. That spends no
tokens, but it queues a job ahead of reviews that have work to do.

The check is substring-based and case-insensitive, so `/REVIEW` and
`/review full` both pass.

To change the policy, edit the `if:` on the `review` job:

- **Everyone:** drop the author-association checks so any PR or slash command
  runs a review. Keep the slash-command clause, or every comment starts a job.
  This suits an open project, but on a hosted provider it means anyone can
  start a paid run.
- **Returning contributors too:** add `CONTRIBUTOR` to also auto-review anyone
  who has committed to the repository before.
- **Admins only:** keep only `OWNER` (plus `MEMBER` for your organisation).

For extra guardrails, require approval for fork-PR workflow runs in
**Settings → Actions → General → Fork pull request workflows**. You can also
move the provider key behind a protected `environment`.
[Trust and Cost](../explanation/trust-and-cost.md) explains the reasoning
behind these options.

## Minimal Workflow: openai

```yaml
name: lgtmaybe

on:
  pull_request_target:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write

# One run per PR and event type: a new push cancels an in-flight review, but
# the overview's own issue_comment event cannot cancel the review that posted it.
concurrency:
  group: lgtmaybe-${{ github.event.pull_request.number || github.event.issue.number }}-${{ github.event_name }}
  cancel-in-progress: true

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
      - uses: actions/checkout@v7 # base repo only, for .lgtmaybe.yml
      - uses: MattJColes/lgtmaybe@v2
        with:
          provider: openai
          model: gpt-5.5
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

What each part does:

- `on:` runs the job when a PR is opened, reopened or pushed to
  (`pull_request_target`), and when someone posts a comment (`issue_comment`).
- `permissions:` lets the job read the repository and write review comments on
  the PR.
- `concurrency:` keeps one run per PR and event type. A new push cancels an
  in-flight review.
- `if:` is the trust gate described in
  [Who Can Trigger a Review](#who-can-trigger-a-review).
- `actions/checkout` checks out the base repo only, so lgtmaybe can read your
  `.lgtmaybe.yml`.
- `api_key` reads the `OPENAI_API_KEY` repository secret.

## Choose the GitHub Author

The minimal workflow uses GitHub's built-in workflow token and posts as
`github-actions[bot]`. This is the default and needs no App installation.

To post as `lgtmaybe[bot]`:

1. Install the public lgtmaybe App on selected repositories.
2. Add `id-token: write` to the workflow.
3. Set `github_identity: lgtmaybe`.

The provider, model, and provider key stay where they are. [Post as lgtmaybe[bot]](./post-as-a-github-app.md) has the complete
workflow, the permission boundary, and uninstall instructions.

If your organisation runs its own GitHub App, the same guide covers the
advanced `app_id` / `app_private_key` inputs.

## Other Key-Based Providers

Swap the `provider`, `model`, and `api_key` inputs:

```yaml
# anthropic
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: ${{ secrets.ANTHROPIC_API_KEY }}

# openrouter
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: openrouter
    model: anthropic/claude-sonnet-4.6
    api_key: ${{ secrets.OPENROUTER_API_KEY }}

# zai (GLM / Zhipu AI)
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: zai
    model: glm-4.6
    api_key: ${{ secrets.ZAI_API_KEY }}

# openai-compatible (here DeepSeek's hosted API)
- uses: MattJColes/lgtmaybe@v2
  with:
    provider: openai-compatible
    model: deepseek-chat
    api_base: https://api.deepseek.com/v1
    api_key: ${{ secrets.OPENAI_COMPATIBLE_API_KEY }}
```

For each one:

1. Create an API key in the provider's console.
2. Add it as a repository secret
   (**Settings → Secrets and variables → Actions**).
3. Reference it as `api_key`.

## Keyless Cloud Workflows

Bedrock (AWS [OIDC](../reference/glossary.md#oidc)), Vertex (GCP [WIF](../reference/glossary.md#wif)), and Azure
(Entra OIDC) need **no API keys in secrets**. Pass `aws_role_arn`,
`gcp_wif_provider`, or `azure_client_id`, and the action performs the keyless
token exchange for you. All three need the `id-token: write` permission. See:

- [Review with Bedrock OIDC](./review-with-bedrock-oidc.md)
- [Review with Vertex WIF](./review-with-vertex-wif.md)
- [Review with Azure OpenAI](./review-with-azure.md)

## Action Inputs

Every input is optional. Leave one empty to use the default shown. Most review
settings can also go in `.lgtmaybe.yml`; an input you set overrides the file.

### Provider and Model

| Input | Default | Description |
|---|---|---|
| `provider` | none | One of `openai`, `openrouter`, `anthropic`, `zai`, `bedrock`, `vertex`, `azure`, `ollama`, `openai-compatible` |
| `model` | none | Model identifier for the chosen provider |
| `api_key` | none | API key for key-based providers, including hosted `openai-compatible` endpoints. Leave empty for bedrock, vertex, ollama, keyless azure, and keyless local servers |
| `api_base` | none | Resource endpoint for azure (`https://<resource>.openai.azure.com`), the `/v1` base for `openai-compatible` (for example `https://api.deepseek.com/v1`), or a custom base URL for another provider |
| `fallback_model` | none | Model to re-run a call on when `model` cannot answer it. For a truncated reply it is the *last* remedy, tried only after a smaller payload, a lower `reasoning_effort`, and reasoning switched off have failed on `model`; for any other failure lgtmaybe switches to it straight away. Overrides `fallback_model` in `.lgtmaybe.yml`. See [Configure fallback_model](configure-lgtmaybe-yml.md#fallback_model) for provider scope, cost, and failure behaviour |
| `triage_model` | none (no triage) | Cheap model that runs first to skip plainly non-substantive files and rank the rest by risk. Security-relevant files always escalate past triage |
| `reflect_model` | `model` | Model for the [self-reflection](../reference/glossary.md#reflection) (false-positive audit) pass. Point it at a stronger model to audit a weaker reviewer's findings |
| `language` | English | Human language for finding titles and bodies and the describe/diagram text (for example `Japanese`). Paths, severities, and suggested code are unchanged |

### What Runs

These inputs choose what the review does. A [lens](../reference/glossary.md#lens) is one focused
review question for the model, such as security or correctness. The
[preset](../reference/glossary.md#preset) sets how many model calls the lenses take.

| Input | Default | Description |
|---|---|---|
| `preset` | `fast` | `fast` runs five calls (security, correctness, code health, artefacts for tests and documentation, and architecture) on every provider; `full` runs one call per lens. An explicit `categories` list in `.lgtmaybe.yml` overrides it |
| `incremental` | auto | Review only the commits added since the last completed review on `synchronize` pushes, and the whole PR elsewhere. `true`/`false` forces it. Falls back to a full review after a force-push or when there is no earlier review. `/review full` forces a full re-review on demand |
| `recursive` | `true` | Review a file whose diff exceeds `max_input_tokens` [hunk](../reference/glossary.md#hunk) by hunk instead of sending it whole. Set `false` to disable |
| `spec_review` | `true` | Check the diff against a committed spec (OpenSpec, GitHub Spec Kit, Kiro). Runs only when a spec is detected and matches the PR, so a repository without specs pays nothing |
| `static_analysis` | `true` | Run deterministic tools (ruff, bandit, mypy, gitleaks, zizmor, ast-grep, osv-scanner, semgrep) in a sandbox with no network over the changed files. Every tool feeds the model untrusted hints to confirm or discard; set `static_analysis.tool_mode` in `.lgtmaybe.yml` to make a tool post directly. Set `false` to turn it off. The image bundles every tool and an offline vulnerability database |
| `mid_review_retrieval` | `false` | Let a lens ask once for read-only files outside the diff, then re-run with them, instead of hedging a cross-file finding. Costs at most one extra call per batch and lens |
| `symbol_resolution` | `true` | During [reflection](../reference/glossary.md#reflection), use ast-grep in a read-only shallow clone of the base branch to find the definition of a symbol a finding depends on, so cross-file findings are re-judged against the real code |
| `learn_feedback` | `true` | On a re-run, suppress a finding a human reacted 👎 to on its inline comment. Resolving a thread is not a suppress signal (it means fixed) |
| `resolve_fixed` | `true` | Resolve a finding's conversation once a re-run no longer reports it and the thread is outdated. Set `false` to resolve by hand |

### What Gets Posted

| Input | Default | Description |
|---|---|---|
| `auto_diagram` | `true` | On an opened, reopened, or pushed-to PR, post or refresh the change overview comment. Set `false` to opt out of the whole comment |
| `auto_describe` | `true` | Head the change overview with a structured description (title, change type, summary, per-file walkthrough, intent check). Set `false` to drop the section; `/describe` still works |
| `high_impact` | `true` | Include the High Impact Areas section: infrastructure, security posture, outage risk, data migrations, backups and recovery, compatibility, observability, dependencies, cost, and compliance. Set `false` to drop the section and its model call |
| `risk` | `true` | Rate the PR's risk of change (low/medium/high/critical) from importer counts, sensitive paths, size, and untested code. Deterministic, no model call. Shown on the summary line, in the overview, as a hidden `lgtmaybe-risk` marker, and (with `pr_labels`) as a `risk/<level>` label |
| `pr_labels` | `false` | Attach derived labels: `review-effort/1-5`, `possible-security-issue`, `consider-splitting`, and `risk/<level>`. Best-effort, no extra model calls |
| `fail_on` | off | Merge-gate threshold (`info`/`low`/`medium`/`high`/`critical`). Creates a `lgtmaybe` Check Run that **fails** when any finding is at or above this severity. See [Gate merges on findings](#gate-merges-on-findings) |
| `profile` | `false` | Print a timing profile in the Action log: per-stage and per-call tables with token and prompt-cache usage |

### Limits and Model Tuning

| Input | Default | Description |
|---|---|---|
| `timeout` | provider default (ollama/openai-compatible/openrouter 1800s, cloud 600s) | Enforced wall-clock timeout for each model call. For ollama and openai-compatible, which may be a single-slot server that queues calls, the default is multiplied by the concurrency, then capped at `max_review_seconds` but never below 1800s. Transient failures (capacity 429s, connection blips, 5xx) are retried with exponential backoff. Permanent ones (bad key, quota or billing 429, unknown model) and a call that uses up this whole timeout fail fast: re-sending the same request against the same budget would only burn it twice |
| `max_review_seconds` | `3600` | Soft wall-clock ceiling for the whole review. Once it passes, queued calls are skipped and partial results post with a notice. A cancelled or timed-out job does the same (on SIGINT/SIGTERM). `0` disables |
| `max_review_tokens` | `0` (off) | Soft ceiling on billable tokens (input plus output) across every model call. Once it passes, no further calls start and the review posts what it has with a notice. Run once with `profile: true`, read the total, then set this above it |
| `max_concurrency` | `6` | Concurrent model calls across the whole review. Set `1` for a serial local run |
| `max_input_tokens` | `100000` | Token budget per model call before the diff is split into batches. Left unset, it is lowered to fit the model's context window when that is known |
| `max_tokens` | 16384 on ollama, openai-compatible and openrouter; the model's own ceiling elsewhere | Cap on the tokens each call may generate. `0` sends no cap. OpenRouter reserves prompt plus `max_tokens` against a prepaid balance, so the finite default keeps that bounded; raise it when a long answer is being cut off |
| `max_findings_per_lens` | `50` | Ceiling on findings from one (batch, lens) call, a backstop against a degenerate reply. When it fires, the highest-severity findings are kept and the review names the lens and the count dropped. `0` disables |
| `reasoning_effort` | route default | Bound how much a reasoning model thinks per call: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `default`. Use it when lens calls truncate or run long |
| `temperature` | `0.0` | Sampling temperature (`0.0` is deterministic) |
| `num_ctx` | `32768` | ollama context window (ignored for hosted providers) |
| `structured_output` | `true` | Constrain output to the findings JSON schema via `response_format` (JSON mode). Set `false` for an `openai-compatible` gateway that rejects it |
| `repair_unparseable` | `true` | When a reply cannot be parsed, send it back once (with the schema, without the diff) to be reformatted. Costs nothing on a healthy run |
| `retry_without_schema` | `true` | If the reformat also fails, re-run that lens once without `response_format`. Only applies to calls that sent the schema |

### Cloud Auth

| Input | Default | Description |
|---|---|---|
| `aws_role_arn` | none | IAM role ARN to assume via OIDC for bedrock (keyless) |
| `aws_region` | `us-east-1` | AWS region for bedrock |
| `gcp_wif_provider` | none | Workload Identity Federation provider resource name for vertex |
| `gcp_service_account` | none | GCP service account email to impersonate via WIF |
| `azure_client_id` | none | Entra (Azure AD) client ID with a federated credential, for keyless azure via OIDC |
| `azure_tenant_id` | none | Entra (Azure AD) tenant ID for keyless azure |

### GitHub Identity and Runtime

| Input | Default | Description |
|---|---|---|
| `config_path` | `.lgtmaybe.yml` | Path to the config file, relative to the repo root. With the default, a missing file is fine; a path you set must exist |
| `github_token` | `${{ github.token }}` | Token for reading the PR and posting the review |
| `github_identity` | `actions` | Comment author: `actions` posts as `github-actions[bot]`; `lgtmaybe` posts as `lgtmaybe[bot]` once the public App is installed and `id-token: write` is granted |
| `identity_broker_url` | managed endpoint | Advanced override for the public App identity exchange |
| `app_id` | none | Advanced: ID of your own GitHub App, used with `app_private_key`. Do not combine with `github_identity: lgtmaybe` |
| `app_private_key` | none | Advanced: private key of your own App named by `app_id` |
| `app_owner` | current repo's owner | Owner for a cross-repo App token |
| `app_repositories` | current repo | Repositories the App token may access, newline- or comma-separated. Use with `app_owner` |
| `image` | `ghcr.io/mattjcoles/lgtmaybe:v2` | Override the container image (advanced) |

The action passes `GITHUB_TOKEN` and the provider credentials into the
container itself, so you do not set them as `env`.

## Gate Merges on Findings

Set [`fail_on`](../reference/glossary.md#fail-on) to a [severity](../reference/glossary.md#severity) to turn the
review into a merge gate. After posting the review, lgtmaybe creates a
**Check Run** named `lgtmaybe`: a pass or fail result GitHub shows on the PR.
Its conclusion is `failure` when any surviving finding is at or above that
severity, and `success` otherwise. lgtmaybe never sets PR approval state, so a
clean review stays comment-only.

```yaml
permissions:
  contents: read
  pull-requests: write
  checks: write # needed to create the Check Run

# ...

- uses: MattJColes/lgtmaybe@v2
  with:
    provider: openai
    model: gpt-5.5
    api_key: ${{ secrets.OPENAI_API_KEY }}
    fail_on: high # block merge on any high/critical finding
```

To make it block merges, mark the check as required in **branch protection**:

1. Open **Settings → Branches → Branch protection rules** (or a ruleset) for the
   target branch.
2. Enable **Require status checks to pass before merging**.
3. Search for and add the **`lgtmaybe`** check. It appears in the list once the
   Action has run with `fail_on` set on a PR against that branch.

A PR with a finding at or above the threshold then shows a failing `lgtmaybe`
check. It cannot merge until the finding is resolved, or until `fail_on` is set
to a higher severity (for example `high` to `critical`).

Leave `fail_on` unset (the default) to keep reviews advisory. lgtmaybe then
creates no check run.

Creating the Check Run requires `checks: write`, as in the snippet above. The
public `lgtmaybe[bot]` App intentionally does not hold that permission, so
`github_identity: lgtmaybe` cannot be combined with `fail_on`. Use the default
Actions identity or a self-managed App granted `Checks: write`.

### What the Public App Cannot Do

The public `lgtmaybe[bot]` App holds a deliberately minimal permission set, so
two features are unavailable under `github_identity: lgtmaybe`:

| Feature | Behaviour under the public App |
|---|---|
| `fail_on` (merge-gate Check Run) | Refused up front with a setup error, because it needs `checks: write` |
| `resolve_fixed` (auto-resolve fixed conversations) | GitHub refuses `resolveReviewThread`, so threads stay open. The review is unaffected: nothing is posted to the thread, the refusal is logged once with what to do, and the run continues |

Both work under the default `actions` identity (with `pull-requests: write` in
the workflow `permissions:` block) or a self-managed App holding the matching
permission. Set `resolve_fixed: false` to stop attempting it.

## Adding a Config File

Place a `.lgtmaybe.yml` at the repo root to control severity thresholds, path
filters, file caps, and more. Set `config_path` if it lives elsewhere.
[Configure .lgtmaybe.yml](./configure-lgtmaybe-yml.md) lists all options.

## Pin to a Specific Version

`@v2` is a floating tag: it tracks the latest `v2.x.x` release. To pin the
action exactly, use a full version tag:

```yaml
uses: MattJColes/lgtmaybe@v2.11.1
```

The action's default `image` is the floating `ghcr.io/mattjcoles/lgtmaybe:v2`.
Pinning the tag alone therefore still runs the newest v2 container. To pin the
reviewer too, set the image to the matching version tag (no `v` prefix):

```yaml
uses: MattJColes/lgtmaybe@v2.11.1
with:
  image: ghcr.io/mattjcoles/lgtmaybe:2.11.1
  # ...provider, model, api_key as usual
```

## On Another Code Host

The review is identical on GitLab and Gitea. Only the wiring and a few
differences in what each [code host](../reference/glossary.md#code-host) supports change:

- [Review on GitLab](./review-on-gitlab.md): a CI job running
  `lgtmaybe gitlab-ci` on merge request pipelines.
- [Review on Gitea](./review-on-gitea.md): Gitea Actions, running the same
  container as GitHub.

Keyless cloud auth (Bedrock, Vertex, Azure) relies on GitHub Actions' OIDC
token, so it is available only on GitHub. On the other hosts use an API-key
provider or `ollama`.
