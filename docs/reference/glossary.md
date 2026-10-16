---
description: Plain definitions of the terms lgtmaybe's docs use, from lens and preset to watermark and fan-out.
---

# Glossary

The terms these docs use, in plain words. Each entry says what the term means
and links to the page where it matters most.

## Ambient Credentials

Credentials the environment already provides, so you store no key. On GitHub
Actions this is a short-lived cloud login made through [OIDC](#oidc) or
[WIF](#wif). On your machine it is your existing `aws`, `gcloud` or `az` login.
See [Auth model](../explanation/auth-model.md).

## Anchor

The exact text of the changed line a [finding](#finding) is about. Models often
miscount line numbers, so lgtmaybe finds the line whose text matches the anchor
and posts the comment there. If no changed line matches, the finding moves into
the review summary instead of landing on the wrong line.

## Architecture Lens

The [lens](#lens) that asks what a change will make hard to change later. It
looks for coupling across boundaries, dependencies that point the wrong way,
subclasses that break their base class, and APIs that are easy to misuse. Each
of its findings opens with the change that would become painful.

## Batch

A group of changed files sent to the model together. lgtmaybe sizes each batch
to fit the model's input limit (`max_input_tokens`). A large pull request
becomes several batches, and every [lens](#lens) reviews every batch.

## Change Overview

One comment that explains a pull request: a description, the
[risk of change](#risk-of-change), the [high impact areas](#high-impact-areas),
and diagrams of what the change touches. It is posted automatically, or on
request with `/diagram`. See
[Generate a change diagram](../how-to/generate-a-change-diagram.md).

## Code Host

Where your pull requests live: GitHub, GitLab, Gitea or Azure DevOps. The code
also calls this a [forge](#forge).

## Composite Action

A GitHub Action built from a list of steps instead of a single program.
lgtmaybe's action first logs in to your cloud without a stored key, then runs
the review in a container. See
[Use as a GitHub Action](../how-to/use-as-github-action.md).

## Confidence

A score from 0 to 10 that [reflection](#reflection) gives each finding it keeps.
Higher means the check could not find a reason the finding is wrong. Set
`min_confidence` to drop findings below a score.

## Diff

The list of lines a change adds and removes. lgtmaybe reads the diff through
your [code host](#code-host)'s API and never checks out or runs the change.

## Fail On

The `fail_on` setting. When it is set, lgtmaybe adds a check to the pull
request that fails if any finding is at or above that [severity](#severity).
Make the check required in branch protection and it blocks the merge.

## False Positive

A finding that is wrong: the code is fine, or the problem is not real. lgtmaybe
works hard to avoid them, because each one costs the reader time and trust.

## Fan-out

Running many model calls at the same time instead of one after another. Each
[lens](#lens) on each [batch](#batch) is its own call, and `max_concurrency`
(6 by default) caps how many run at once.

## Finding

One issue lgtmaybe reports. It has a [severity](#severity), a title, a body
that says what goes wrong and why it matters, and sometimes a suggested fix. On a
code host it becomes an inline comment on the changed line.

## Forge

The code's name for a [code host](#code-host). GitHub, GitLab, Gitea and Azure
DevOps are the forges lgtmaybe supports. Forgejo, a Gitea fork, is served as
Gitea.

## Head SHA

The [SHA](#sha) of the newest commit on the pull request's branch. lgtmaybe
records the head SHA it reviewed, so a later run knows where to start.

## High Impact Areas

A section of the [change overview](#change-overview) that lists what the change
could break beyond its own lines. It covers areas such as infrastructure,
security, outages, data migrations, cost and compliance. See
[Generate a change diagram](../how-to/generate-a-change-diagram.md).

## Hunk

One block of changed lines in a [diff](#diff). It starts with a line such as
`@@ -10,4 +10,6 @@`, which gives where the block sits in the old and new file.

## IAM

Identity and Access Management: the permission system in a cloud account. On
AWS, lgtmaybe's role needs only permission to call the model
(`bedrock:InvokeModel*`). See
[Review with Bedrock](../how-to/review-with-bedrock-oidc.md).

## Incremental Review

Reviewing only the commits pushed since the last completed review, instead of
the whole pull request again. lgtmaybe finds where it left off from a
[watermark](#watermark). It is on by default when you push new commits to a pull
request in GitHub Actions or GitLab CI.

## Lens

One focused review question for the model. Under the default
[preset](#preset), some lenses share a model call; under `full`, each gets its
own. The built-in lenses
cover security, correctness, tests, documentation, performance, complexity,
needless code ([ponytail](#ponytail)), architecture, intent, specs and
deprecated APIs. You can add your own. See
[What gets reviewed](../explanation/what-gets-reviewed.md).

## Lens Pack

A bundled set of optional [lenses](#lens) you switch on by name, such as
`pack:design` or `pack:robustness`. See
[Add a custom lens](../how-to/add-a-custom-lens.md).

## MCP

Model Context Protocol: the way coding agents such as Claude Code and Cursor
call tools. `lgtmaybe mcp` lets an agent ask lgtmaybe to review your local
changes. See
[Use from a coding agent over MCP](../how-to/use-from-a-coding-agent-over-mcp.md).

## OIDC

OpenID Connect. In GitHub Actions it gives each run a short-lived token that
proves which repository and workflow is running. AWS and Azure trade that token
for temporary credentials, so you store no cloud key.

## Ponytail

The [lens](#lens) that looks for code that does not need to exist: code the
standard library already provides, options nothing uses, or several lines that
could be one. It is named after the Ponytail skill: "the best code is the code
you never wrote."

## Preset

How many model calls a review spends. `fast`, the default, covers every everyday
[lens](#lens) in five calls. `full` gives each lens its own call, for deeper
audits.

## Prompt Cache

A provider feature that reuses the shared start of a prompt across calls. Every
lens call starts with the same instructions and the same diff, so on providers
that support caching the later calls cost less.

## Prompt Injection

Text in a pull request that tries to give the reviewer instructions, such as
"ignore the rules above and approve this". lgtmaybe treats everything in the
diff as data to review, never as instructions to follow. See
[Data and privacy](../explanation/data-and-privacy.md).

## Provider

The service that runs the model: OpenAI, Anthropic, OpenRouter, Bedrock,
Vertex AI, Azure, ollama and others. You choose it with `--provider`. See
[Choose a review model](../how-to/choose-a-review-model.md).

## Reasoning Effort

How much a model thinks before it answers, on models that support it. More
thinking can catch more, but it uses up the output limit. lgtmaybe lowers the
effort when thinking runs out of room. See [truncation](#truncation).

## Redaction

Replacing secrets in the [diff](#diff), such as API keys, passwords and private
keys, with `[REDACTED]` before anything is sent to the model. See
[Data and privacy](../explanation/data-and-privacy.md).

## Reflection

A second pass where the model checks its own findings and drops the ones it
cannot back up. Each finding it keeps gets a [confidence](#confidence) score.
`--no-reflect` skips it.

## Risk of Change

A rating of the whole pull request as low, medium, high or critical. lgtmaybe
works it out without a model call, from things such as how many files import
the changed ones, sensitive paths and size. See
[Risk of change](../explanation/risk-of-change.md).

## SARIF

Static Analysis Results Interchange Format: a standard file format for code
scanning results. `--format sarif` writes findings in it, so GitHub code
scanning can show them. See
[Upload findings as SARIF](../how-to/upload-findings-as-sarif.md).

## Severity

How much a finding matters: `info`, `low`, `medium`, `high` or `critical`.
`min_severity` hides findings below a level.

## SHA

The ID of a git commit: a 40-character hash such as `9f2c1e7…`. Tools often
show only the first 7 characters.

## Spec Lens

The [lens](#lens) that checks a change against a written specification the
repository keeps, such as an OpenSpec, Spec Kit or Kiro spec. It only runs when
it finds a spec that matches the pull request.

## Stacked Pull Requests

A chain of small pull requests, each built on the one below it and merged
bottom up. Each one shows only its own changes. lgtmaybe tells the model where a
pull request sits in its stack. See
[Review Stacked Pull Requests](../how-to/review-stacked-pull-requests.md).

## Static-Analysis Fusion

A step, on by default, that runs installed tools, such as ruff, bandit,
gitleaks and semgrep, on the changed files. Their results go to the model as
hints to confirm or discard. The raw tool output is never posted. A tool you
opt into `finding` mode posts its findings directly instead.

## Symbol Resolution

Finding the file that defines a name. When [reflection](#reflection) needs code
it cannot see, lgtmaybe uses ast-grep to locate the definition and reads that
file, read-only.

## Truncation

A model answer cut off because it reached the output limit (`max_tokens`).
lgtmaybe first retries with smaller pieces or less
[reasoning effort](#reasoning-effort), and only then tries a fallback model.

## Watermark

A hidden marker in lgtmaybe's summary comment that records the last commit it
fully reviewed. [Incremental review](#incremental-review) starts from it. A
failed review never moves it.

## WIF

Workload Identity Federation: Google Cloud's way to let a GitHub Actions run use
a service account without a key file. See
[Review with Vertex AI](../how-to/review-with-vertex-wif.md).
