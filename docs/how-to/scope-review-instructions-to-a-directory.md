---
description: Scope review instructions and reference files to part of a monorepo with directory_rules — strict on payments/**, lenient on tests/**, with an architecture doc read before src/**.
---

# Scope review instructions to a directory

A monorepo is not uniform. `payments/**` deserves a strictness that would be
pure noise on `tests/**`, and reviewing `src/**` well may need a design document
the diff never shows. `directory_rules` scopes both **extra instructions** and
**reference files** to path globs, so one repo config can say different things
about different directories.

## Contents

- [The shape of a rule](#the-shape-of-a-rule)
- [Scope instructions to a directory](#scope-instructions-to-a-directory)
- [Give the reviewer background reading](#give-the-reviewer-background-reading)
- [How rules reach the model](#how-rules-reach-the-model)
- [Limits](#limits)
- [Security: rules are trusted configuration](#security-rules-are-trusted-configuration)
- [See also](#see-also)

## The shape of a rule

| Field | Required | What it is |
|---|---|---|
| `paths` | no | fnmatch globs against the repo-relative path. A `**/` prefix also matches at the repo root. **Omit it** (or leave it empty) and the rule applies everywhere. |
| `instructions` | no | Free text handed to every lens reviewing a file the rule matches. |
| `context_files` | no | Repo-relative files whose text is read from the checked-out workspace and included alongside the instructions. |

A rule needs at least one of `instructions` and `context_files` to do anything.

## Scope instructions to a directory

```yaml
provider: bedrock
model: bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0

directory_rules:
  - paths: ["payments/**", "billing/**"]
    instructions: |
      This is money-handling code. Treat any rounding, currency-conversion or
      retry change as high severity, and flag any write path that is not
      idempotent under a duplicated webhook.

  - paths: ["tests/**"]
    instructions: |
      Test code. Do not flag duplication, long functions, or missing
      docstrings here — explicit and repetitive is the house style for tests.
      Do still flag assertions that can never fail.

  - paths: ["infra/**/*.tf"]
    instructions: |
      Terraform. Flag any IAM policy with a `*` action or resource, and any
      storage bucket or database that becomes publicly reachable.
```

Rules are independent, not exclusive: a review batch touching both
`payments/charge.py` and `tests/test_charge.py` gets both rules, in the order
you wrote them.

## Give the reviewer background reading

`context_files` puts a document in front of the model that the diff would never
show it — the architecture the code is supposed to follow, a data-model
invariant, a deprecation plan:

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

The files are read from the workspace lgtmaybe is running in — the same place
your `.lgtmaybe.yml` comes from. They are redacted for secrets before they leave
the process, exactly like the diff.

## How rules reach the model

The matched rules are rendered into a single block that leads each review call,
labelled as trusted configuration so the model never confuses it with the
(untrusted) diff below it. The block joins the same cacheable prefix as the diff
itself, so on providers with prompt caching you pay for it roughly once per
batch rather than once per lens.

Instructions go in verbatim. Context-file text is defanged first, so a document
that happens to quote one of lgtmaybe's own prompt delimiters cannot break out
of its block.

## Limits

- **Instructions and context only.** A rule cannot change `min_severity`,
  `categories`, or any other setting for a directory. If you want a lens that
  runs everywhere with its own worked example, that is
  [a custom lens](add-a-custom-lens.md).
- **The context budget is a slice, not the whole thing.** Context files share
  `max_input_tokens / 8` and are capped at five files per review; the diff
  always dominates the call. A file that does not fit is skipped.
- **A missing path is skipped silently.** A context file is an aid, and a stale
  path in your config must never fail a review. Check `--profile` or the run log
  if a document does not seem to be reaching the model.
- **YAML only.** Like `finding_rules` and `extra_lenses`, `directory_rules` is a
  list of objects, so it has no CLI flag and no Action input. Put it in
  `.lgtmaybe.yml`.

## Security: rules are trusted configuration

Both the instructions and the context text enter the prompt as **trusted**
content, so they must never come from a pull request's author.

They do not. On the recommended `pull_request_target` trigger, the workflow
checks out the **base** branch — the code already merged — and never the PR
head. `directory_rules` and the files it names are read from that checkout, the
same source `.lgtmaybe.yml` and `lens_paths` already come from. lgtmaybe never
fetches context files through the host's API, which would resolve them at the
(untrusted) PR head. The same holds on GitLab and Gitea: context comes from the
checked-out workspace, never from the change's head.

The practical rule: a fork PR can change `ARCHITECTURE.md` in its own branch and
lgtmaybe will still read the base branch's copy. Review changes to your config
and context files the way you review any other change to CI.

## See also

- [Configure `.lgtmaybe.yml`](configure-lgtmaybe-yml.md) — every field
- [Add a custom review lens](add-a-custom-lens.md) — a whole extra lens, repo-wide
- [Configuration reference](../reference/config.md) — generated schema
