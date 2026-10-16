---
description: Run lgtmaybe from the pre-commit framework so every push (or commit) gets a local AI review, optionally blocking on high-severity findings.
---

# Review Before You Commit

lgtmaybe ships two hooks for the [pre-commit](https://pre-commit.com) framework,
so a local review runs from your Git hooks with no extra scripting:

| Hook id | Stage | Runs | Reviews |
|---|---|---|---|
| `lgtmaybe-pre-push` | `pre-push` | `lgtmaybe review` | Your branch's commits against the remote's primary branch |
| `lgtmaybe` | `pre-commit` | `lgtmaybe review --uncommitted` | The changes you are about to commit, against `HEAD` |

Both print findings exactly as `lgtmaybe review` does. Nothing is posted
anywhere.

## Contents

- [Before You Start](#before-you-start)
- [Add the Hook](#add-the-hook)
- [Block on Findings](#block-on-findings)
- [Review Every Commit Instead](#review-every-commit-instead)
- [Use a Keyless Cloud Provider](#use-a-keyless-cloud-provider)
- [Skip the Review Once](#skip-the-review-once)
- [See Also](#see-also)

## Before You Start

- **Each run calls your model.** A review is one or more model calls, so it
  takes time and, on a hosted provider, costs money every time the hook fires.
  That is why `pre-push` (one review per push) is the recommended stage rather
  than `pre-commit` (one review per commit). ollama keeps it local and free.
- **Credentials come from your environment.** The hook runs in your shell's
  environment, so export your provider's key (for example `OPENAI_API_KEY` or
  `ANTHROPIC_API_KEY`) the same way you would for a manual
  `lgtmaybe review`. lgtmaybe never saves API keys in its config.
- **Pick the provider and model once.** Run `lgtmaybe config init` to save a
  default provider and model in your user config, or put them in the repo's
  [`.lgtmaybe.yml`](configure-lgtmaybe-yml.md), or pass them as hook `args`.
- **pre-commit 3.2.0 or later.** The hooks use the `pre-commit` and `pre-push`
  stage names, which arrived in that release.

pre-commit installs lgtmaybe into its own virtual environment from the tagged
source, so you do not need lgtmaybe installed separately for the hook. The
first run takes a minute or two while pre-commit builds that environment.

## Add the Hook

Add this to `.pre-commit-config.yaml` at the root of your repository. Pin `rev`
to a release tag that includes the hooks (v2.12.0 or later);
`pre-commit autoupdate` moves it to the latest release:

```yaml
repos:
  - repo: https://github.com/MattJColes/lgtmaybe
    rev: v2.12.0
    hooks:
      - id: lgtmaybe-pre-push
        args: [--provider, anthropic, --model, claude-sonnet-4-6, --fail-on, high]
```

Then install the Git hook for the `pre-push` stage:

```bash
pre-commit install --hook-type pre-push
```

The next `git push` reviews your branch first. To try it without pushing:

```bash
pre-commit run lgtmaybe-pre-push --hook-stage pre-push
```

Any `lgtmaybe review` flag works in `args`. For example, add `--base`,
`origin/develop` when your branch targets something other than the remote's
default branch, or `--min-severity`, `medium` to print less.

## Block on Findings

Without `--fail-on` the hook is advisory: it prints the review and lets the
push through. Both hooks set `verbose: true`, so the findings show even when
the hook passes.

With `--fail-on <severity>` (as in the example above), `lgtmaybe review` exits
with exit code 3 when any finding is at or above that severity, after printing
the findings, so pre-commit stops the push. A review that could not run at all
(for example, missing credentials or an unreachable model) exits 1, which also
stops the push; a usage error exits 2.

Only the command-line flag gates a local review. A `fail_on` set in
`.lgtmaybe.yml` is the GitHub Action's merge gate and never changes the exit
code of a local run, so configuring CI does not suddenly block your pushes.

## Review Every Commit Instead

To review each commit as you make it, use the `lgtmaybe` hook and install the
default hook type:

```yaml
repos:
  - repo: https://github.com/MattJColes/lgtmaybe
    rev: v2.12.0
    hooks:
      - id: lgtmaybe
```

```bash
pre-commit install
```

While hooks run, pre-commit sets your unstaged edits aside, so the review covers
what you have staged. Untracked files in the working tree are reviewed too.
Because this calls your model on every commit, prefer a local model (see
[Run locally](run-locally.md)) or the `pre-push` hook.

## Use a Keyless Cloud Provider

The hook installs the base package, which covers every API-key provider plus
ollama and OpenAI-compatible servers. For keyless Bedrock, Vertex or Azure, add
the cloud SDK the matching extra would install:

```yaml
      - id: lgtmaybe-pre-push
        args: [--provider, bedrock, --model, us.anthropic.claude-sonnet-4-6]
        additional_dependencies: [boto3]   # vertex: google-auth, azure: azure-identity
```

Your ambient credentials (`~/.aws`, `gcloud auth application-default login`,
`az login`) are used as they are for a manual run.

## Skip the Review Once

pre-commit's usual escape hatches apply:

```bash
SKIP=lgtmaybe-pre-push git push   # skip only this hook
git push --no-verify              # skip every pre-push hook
```

If you use [pre-commit.ci](https://pre-commit.ci), add the hook id to its
`skip` list: it has no model credentials, so the review cannot run there. Use
the [GitHub Action](use-as-github-action.md) to review pull requests in CI.

## See Also

- [Install the CLI](install-the-cli.md) - the same `lgtmaybe review` the hooks run
- [Fix findings with an AI agent](fix-findings-with-an-ai-agent.md) - turn local findings into corrections
- [Reduce review cost](reduce-review-cost.md) - make each run cheaper
