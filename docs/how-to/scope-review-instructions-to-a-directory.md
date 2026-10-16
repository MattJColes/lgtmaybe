---
description: "Scope review instructions and reference files to part of a monorepo with directory_rules - strict on payments/**, lenient on tests/**, and an architecture doc read before src/**."
---

# Scope Review Instructions to a Directory

A monorepo is not uniform. `payments/**` deserves a strictness that would be
pure noise on `tests/**`. And reviewing `src/**` well may need a design document
the diff never shows.

`directory_rules` scopes both **extra instructions** and **reference files** to
path globs (wildcard patterns such as `src/**`). So one repo config can say
different things about different directories.

## Contents

- [The Shape of a Rule](#the-shape-of-a-rule)
- [Scope Instructions to a Directory](#scope-instructions-to-a-directory)
- [Give the Reviewer Background Reading](#give-the-reviewer-background-reading)
- [How Rules Reach the Model](#how-rules-reach-the-model)
- [Limits](#limits)
- [Security: Rules Are Trusted Configuration](#security-rules-are-trusted-configuration)
- [See Also](#see-also)

## The Shape of a Rule

| Field | Required | What it is |
|---|---|---|
| `paths` | no | fnmatch globs against the repo-relative path. A `**/` prefix also matches at the repo root. **Omit it** (or leave it empty) and the rule applies everywhere. |
| `instructions` | no | Free text handed to every [lens](../reference/glossary.md#lens) call whose [batch](../reference/glossary.md#batch) contains a file the rule matches. |
| `context_files` | no | Repo-relative files whose text is read from the checked-out workspace and included alongside the instructions. |

A rule needs at least one of `instructions` and `context_files` to do anything.

## Scope Instructions to a Directory

```yaml
provider: bedrock
model: us.anthropic.claude-sonnet-4-6

directory_rules:
  - paths: ["payments/**", "billing/**"]
    instructions: |
      This is money-handling code. Treat any rounding, currency-conversion or
      retry change as high severity, and flag any write path that is not
      idempotent under a duplicated webhook.

  - paths: ["tests/**"]
    instructions: |
      Test code. Do not flag duplication, long functions, or missing
      docstrings here. Explicit and repetitive is the house style for tests.
      Do still flag assertions that can never fail.

  - paths: ["infra/**/*.tf"]
    instructions: |
      Terraform. Flag any IAM policy with a `*` action or resource, and any
      storage bucket or database that becomes publicly reachable.
```

Rules are independent, not exclusive. lgtmaybe reviews files in batches. A rule
applies to a whole batch when any file in it matches. A batch touching both
`payments/charge.py` and `tests/test_charge.py` gets both rules, in the order
you wrote them.

## Give the Reviewer Background Reading

`context_files` puts a document in front of the model that the diff would never
show it. Examples are the architecture the code should follow, a data-model
invariant, or a deprecation plan:

```yaml
directory_rules:
  - paths: ["src/**"]
    instructions: |
      Check changes against the architecture described below. Flag a new
      import that crosses a boundary the document forbids.
    context_files:
      - ARCHITECTURE.md
      - docs/data-model.md
```

Paths are repo-relative. The files are read from the workspace lgtmaybe runs
in, the same place your `.lgtmaybe.yml` comes from. They are
[redacted](../reference/glossary.md#redaction) for secrets before they leave the
process, exactly like the diff. A path that resolves outside the workspace is
skipped.

## How Rules Reach the Model

The rules matched for a batch are rendered into one block. The block sits ahead
of the diff in each review call. It is labelled as trusted configuration, so the
model never confuses it with the untrusted diff after it.

The block is part of the same cacheable prefix as the diff. So on providers with
[prompt caching](../reference/glossary.md#prompt-cache), you pay for it roughly
once per batch rather than once per lens.

Instructions and context text both go in as written, with one exception: any
of lgtmaybe's own prompt delimiters are neutralised. So a document that happens
to quote one can't break out of its block.

## Limits

- **Instructions and context only.** A rule cannot change `min_severity`,
  `categories`, or any other setting for a directory. If you want a lens that
  runs everywhere with its own worked example, that is
  [a custom lens](add-a-custom-lens.md).
- **The context budget is small.** Context files share `max_input_tokens / 8`
  (12,500 tokens at the default). They are capped at five files per review. So
  the diff always dominates the call. A file that doesn't fit is skipped whole.
- **A missing path is skipped silently.** A context file is an aid, and a stale
  path must never fail a review. lgtmaybe doesn't log which files it skipped. If
  a document doesn't seem to reach the model, check that the path is
  repo-relative and that the file fits the budget.
- **YAML only.** Like `finding_rules` and `extra_lenses`, `directory_rules` is a
  list of objects. So it has no CLI flag and no Action input. Put it in
  `.lgtmaybe.yml`.

## Security: Rules Are Trusted Configuration

Both the instructions and the context text enter the prompt as **trusted**
content, so they must never come from a pull request's author.

On the recommended `pull_request_target` trigger, the workflow checks out the
**base** branch (the code already merged), never the PR head. `directory_rules`
and the files it names are read from that checkout. That is the same source as
`.lgtmaybe.yml` and `lens_paths`. lgtmaybe never fetches context files through
the host's API, which would resolve them at the untrusted PR head.

On every host, context comes only from the job's workspace. So it is as trusted
as whatever your job checks out. The [GitLab example job](review-on-gitlab.md)
doesn't clone the repository at all. So it has no `.lgtmaybe.yml` or context
files to read.

In practice, a fork PR can change `ARCHITECTURE.md` in its own branch, and
lgtmaybe still reads the base branch's copy. Review changes to your config and
context files the way you review any other change to CI.

## See Also

- [Configure `.lgtmaybe.yml`](configure-lgtmaybe-yml.md) - every field
- [Add a custom review lens](add-a-custom-lens.md) - a whole extra lens, repo-wide
- [Calibrate reviews to your project](calibrate-reviews-to-your-project.md) - stage, domain and notes for the whole repo
- [Configuration reference](../reference/config.md) - generated schema
