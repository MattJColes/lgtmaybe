---
description: Add a custom review lens to lgtmaybe - a bring-your-own skill file that runs alongside the built-in lenses.
---

# Add a Custom Review Lens (BYO Skills)

lgtmaybe ships eleven built-in review lenses: security, correctness, deprecation,
tests, documentation, performance, complexity, intent, spec, `ponytail`
(the "lazy senior dev", write-less-code lens), and `architecture` (design
decisions that will be expensive to reverse). A custom lens can't reuse a
built-in id, so a lens you named `architecture` before it became built-in needs
a new id. A **custom lens** adds your own.
It runs alongside the built-ins as its own focused model call, and its findings
merge into the same review. Use one to enforce a house style or team convention
the built-in lenses don't cover.

## Contents

- [How a Lens Works](#how-a-lens-works)
- [Inline in `.lgtmaybe.yml`](#inline-in-lgtmaybeyml)
- [As Reusable Skill Files](#as-reusable-skill-files)
- [Bundled Lens Packs (`pack:<name>`)](#bundled-lens-packs-packname)
- [Run It](#run-it)
- [Security: Lenses Are Trusted Input](#security-lenses-are-trusted-input)
- [See Also](#see-also)

## How a Lens Works

Every lens, built-in or custom, has the same shape: a focused set of
instructions and one worked example, sent as its own model call so the model
concentrates on a single concern. A custom lens has these fields:

| Field | Required | What it is |
|---|---|---|
| `id` | yes | A unique short name. It can't be a built-in category name or start with `scan:`. |
| `instructions` | yes | What to look for, in plain language. This is the lens. |
| `title` | no | A human-readable heading (defaults to `id`). |
| `example_diff` + `example_finding` | no (together) | A worked example: a hunk and the finding the model should return for it. Without one, the lens gets a generic example. Supplying your own noticeably improves smaller models. |

Findings from the lens carry its `id` as their category, so
[`finding_rules`](configure-lgtmaybe-yml.md#finding_rules) can drop or re-grade
them like any built-in lens.

## Inline in `.lgtmaybe.yml`

The quickest way is to define the lens in your repo config:

```yaml
provider: ollama
model: qwen3.6:27b
extra_lenses:
  - id: house-style
    title: House style
    instructions: |
      Enforce our team conventions on changed code. Flag new public functions
      that return bare dicts instead of a typed dataclass, and any logging at
      WARNING or above that doesn't include a correlation id.
    example_diff: |
      --- a/api.py
      +++ b/api.py
      @@ -10,1 +10,2 @@
       def make_user(name):
      +    return {"name": name, "active": True}
    example_finding:
      path: api.py
      line: 11
      severity: low
      title: Public function returns a bare dict
      body: House style returns a typed dataclass from public functions, not a dict.
      suggestion: "    return User(name=name, active=True)"
```

!!! note "The Ponytail lens is built in"
    The "lazy senior dev / write less code" instinct from
    [Ponytail](https://github.com/DietrichGebert/ponytail) ships as the built-in
    `ponytail` lens, so you don't need a custom lens for it. Use `extra_lenses`
    for something the built-ins don't cover, like the house-style rule above.

!!! note "Custom lenses share the never-cut rule"
    Every lens, custom and bundled ones included, runs under the shared rule that
    forbids recommending the removal of trust-boundary validation, data-loss
    error handling, security controls, or accessibility support. Custom lenses
    sit outside the deterministic conflict backstop, which only arbitrates lens
    pairs lgtmaybe declares opposed, so write instructions that don't contradict
    the built-ins. See
    [When Lenses Disagree](../explanation/what-gets-reviewed.md#when-lenses-disagree).

## As Reusable Skill Files

To share lenses across repos, or let an agent harness add its own, put each
lens in its own file and point `lens_paths` at the file or its directory:

```yaml
# .lgtmaybe.yml
provider: ollama
model: qwen3.6:27b
lens_paths:
  - .lgtmaybe/skills
```

```yaml
# .lgtmaybe/skills/house-style.yml
id: house-style
title: House style
instructions: |
  Flag new public functions that return bare dicts instead of a typed dataclass,
  and WARNING+ logging without a correlation id.
```

A skill file holds one lens (a mapping) or several (a list). A directory entry
loads every `*.yml` and `*.yaml` file in it. Paths resolve relative to where
lgtmaybe runs, normally the repo root. Lenses from `lens_paths` are appended to
any inline `extra_lenses`, and every `id` must be unique across the whole set.

## Bundled Lens Packs (`pack:<name>`)

lgtmaybe ships an opt-in library of extra lenses, distilled from the wider
engineering-review ecosystem (Ousterhout, Metz, Carmack, the NASA Power of Ten /
TigerStyle, Google's eng-practices, and several skill and rule collections; see
[ATTRIBUTION.md](https://github.com/MattJColes/lgtmaybe/blob/main/ATTRIBUTION.md)).
They are off by default because they are more opinionated than the built-ins,
and every lens you enable adds a model call per batch. Enable a pack by name
with the `pack:` scheme in `lens_paths`, which works for a `pip install` with no
repo-relative path to point at:

```yaml
# .lgtmaybe.yml
provider: ollama
model: qwen3.6:27b
lens_paths:
  - pack:design       # combine as many as you like
  - pack:robustness
```

| Pack | Lenses | Use it when |
|---|---|---|
| `pack:design` | `wrong-abstraction`, `shallow-module`, `information-leakage`, `errors-out-of-existence`, `hidden-state`, `naming` | You want review of structure and taste at a finer grain than the built-in `architecture` lens: the right abstraction, deep modules, explicit state. |
| `pack:robustness` | `assertions`, `bounded`, `idempotency`, `migrations`, `portability`, `observability` | Operational safety: defensive invariants, bounded work, retry-safe side effects, safe migrations. |
| `pack:interface` | `api-design`, `type-safety`, `magic-values`, `comment-why` | Contracts and clarity: backward compatibility, tight types, named constants, comments that explain why. |
| `pack:frontend` | `accessibility`, `i18n` | The diff touches UI or user-facing copy. |

Each pack is a directory of skill files inside the package, so you can browse or
copy any single lens from
[`src/lgtmaybe/lenses/`](https://github.com/MattJColes/lgtmaybe/tree/main/src/lgtmaybe/lenses).
An unknown `pack:` name fails with a list of the packs that exist.

## Run It

Custom lenses run on every review, CLI or GitHub Action, once they are in your
config. On the CLI, their findings appear alongside the built-ins:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b --api-base http://localhost:11434
```

`categories` controls only the built-in lenses, so you can narrow it and your
custom lenses still run.

## Security: Lenses Are Trusted Input

A lens's instructions and example reach the model as trusted reviewer
instructions, so treat a lens like code you run: define lenses only in files you
control, such as your committed `.lgtmaybe.yml` or repo skill files, and never
source them from a pull request. On `pull_request_target` the Action reads
config from the base branch, not the PR head, so a fork PR can't add or change a
lens. The diff itself is always treated as untrusted data, separately from your
lenses.

## See Also

- [Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md#extra_lenses) - the field reference.
- [What gets reviewed](../explanation/what-gets-reviewed.md) - the built-in lenses.
