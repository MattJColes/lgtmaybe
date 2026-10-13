---
description: Tell lgtmaybe what kind of project it is reviewing with the project block, so a prototype gets fewer nits and a regulated service gets a stricter bar on data handling and risk.
---

# Calibrate Reviews to Your Project

A weekend prototype and a regulated payments service shouldn't get the same
review. The prototype doesn't need to hear about missing metrics, and the
payments service shouldn't have an access-control slip rated as a minor issue.
The `project` block tells the reviewer which kind of repository it is looking
at, and lgtmaybe adjusts both its defaults and the model's guidance to match.

## Contents

- [The Shape of a Profile](#the-shape-of-a-profile)
- [Pick a Stage](#pick-a-stage)
- [Add Domain and Notes](#add-domain-and-notes)
- [What Your Settings Still Control](#what-your-settings-still-control)
- [How the Profile Reaches the Model](#how-the-profile-reaches-the-model)
- [Why Not Read AGENTS.md?](#why-not-read-agentsmd)
- [Security: the Profile Is Trusted Configuration](#security-the-profile-is-trusted-configuration)
- [See Also](#see-also)

## The Shape of a Profile

```yaml
provider: bedrock
model: us.anthropic.claude-sonnet-4-6

project:
  stage: regulated
  domain: payments
  notes: |
    PCI-scoped card flows live under payments/. Most traffic is nightly batch,
    so latency findings on the batch path matter less than correctness.
```

| Field | Required | What it is |
|---|---|---|
| `stage` | no | `prototype`, `production` or `regulated`. Changes defaults and adds fixed guidance. |
| `domain` | no | A short label, up to 80 characters: `payments`, `healthcare`, `internal tooling`. |
| `notes` | no | Free text the reviewer should know, up to 1,000 characters. A longer value fails validation instead of being cut short. |

Every field is optional. Leave the block out and the review is exactly what it
was without it.

## Pick a Stage

| Stage | Default it changes | Guidance the model gets |
|---|---|---|
| `prototype` | [`min_severity`](configure-lgtmaybe-yml.md#min_severity) defaults to `medium` instead of `low` | Report correctness and security problems, and skip hardening, observability, scalability and documentation polish it doesn't need yet. |
| `production` | none | Hold the change to the standard review bar. |
| `regulated` | Any PR that isn't only docs and tests is at least `medium` [risk](../explanation/risk-of-change.md), with a "regulated workload" reason | Where the domain makes them material, rate data handling, auditability, access control and personal-data exposure one severity step higher. |

`production` changes no settings. It's there so a team can say so explicitly,
and so the guidance paragraph has a stage to name alongside `domain` and
`notes`.

The `regulated` risk floor stops at `medium` on purpose. Two distinct `high`
reasons make a change `critical`, so a `high` floor would turn every change to a
widely imported module into `critical`. A PR that only touches documentation
and tests stays `low` in any stage.

## Add Domain and Notes

`domain` and `notes` give the model context it can't infer from a diff. Keep
`notes` to what changes how a finding should be judged: where the sensitive
code lives, which paths are hot, what the team has decided not to care about.
For instructions that only apply to part of the repo, use
[`directory_rules`](scope-review-instructions-to-a-directory.md) instead, which
reaches only the lenses reviewing matching files.

## What Your Settings Still Control

A stage only moves defaults. A value you set yourself always wins, whether it
comes from `.lgtmaybe.yml`, the user config or a CLI flag:

```yaml
project:
  stage: prototype
min_severity: low   # stays low: you set it, so the stage leaves it alone
```

```bash
lgtmaybe review --min-severity info   # beats the prototype default too
```

## How the Profile Reaches the Model

The profile renders one short paragraph at the end of the system prompt that
every review lens shares:

```text
## Project context
From the reviewer configuration (trusted). Calibrate what you report and how
severe you rate it to this project:
This is a regulated workload: where the domain makes them material, rate data
handling, auditability, access control and personal-data exposure one severity
step higher than you otherwise would.
Domain: payments.
Notes from the maintainers: PCI-scoped card flows live under payments/. ...
```

Because it sits in the shared prompt, providers with prompt caching pay for it
once per run rather than once per lens. The reflection pass, the description,
High Impact Areas and the diagrams don't see it.

The stage's settings changes are deterministic. Whether the guidance paragraph
shifts the severities a given model reports depends on the model, and isn't
measured by the eval harness yet.

## Why Not Read AGENTS.md?

Many repositories already have an `AGENTS.md`, `CLAUDE.md` or `SOUL.md`, and
reading one automatically looks like a shortcut. lgtmaybe deliberately doesn't:

- Those files are written for coding agents: build commands, test runners,
  style rules, personas. Little of it tells a reviewer how strict to be, and
  all of it costs tokens on every call.
- Context files share an eighth of `max_input_tokens` (12,500 tokens by
  default), and a file over budget is skipped whole. The repositories with the
  most thorough agent docs are the ones whose file wouldn't fit.
- A stage maps to settings changes that behave the same way every run. Free
  prose can't do that.

If you want one of those files in front of the reviewer anyway, name it in a
`directory_rules` entry with no `paths`, which applies everywhere:

```yaml
directory_rules:
  - context_files: [AGENTS.md]
```

## Security: the Profile Is Trusted Configuration

The paragraph enters the system prompt as trusted content, so it must never come
from a pull request's author. It doesn't: on the recommended
`pull_request_target` trigger, `.lgtmaybe.yml` is read from the checked-out
base branch, never the PR head. `notes` and `domain` still have delimiter
tokens neutralised, so nothing in them can close a prompt block early.

## See Also

- [Configure `.lgtmaybe.yml`](configure-lgtmaybe-yml.md#project), every field
- [Scope review instructions to a directory](scope-review-instructions-to-a-directory.md), for per-path instructions
- [Risk of change](../explanation/risk-of-change.md), how the verdict is built
- [Configuration reference](../reference/config.md), the generated schema
