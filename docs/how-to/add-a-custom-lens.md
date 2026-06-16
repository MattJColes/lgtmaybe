# Add a custom review lens (BYO skills)

lgtmaybe ships nine built-in review lenses (security, correctness, deprecation,
tests, documentation, performance, complexity, intent, and `ponytail` — the
"lazy senior dev" / write-less-code lens). A **custom lens** lets you add your
own — a "skill file" that runs alongside the built-ins, fans out as its own
focused model call, and merges its findings into the same review. Use it to bake
in a house style or a team convention the built-in lenses don't cover.

## How a lens works

Every lens — built-in or custom — is the same shape: a focused instruction set and
one worked example, sent as its own system prompt so the model concentrates on a
single concern. A custom lens needs:

| Field | Required | What it is |
|---|---|---|
| `id` | yes | A unique short name. Must not collide with a built-in category. |
| `instructions` | yes | What to look for, in plain language. This is the lens. |
| `title` | no | Human-readable heading (falls back to `id`). |
| `example_diff` + `example_finding` | no (together) | A worked example: a hunk and the finding the model should return for it. Optional, but it sharply improves smaller models. |

## Inline in `.lgtmaybe.yml`

The quickest way — define the lens directly in your repo config:

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
    `ponytail` lens — you don't need a custom lens for it. Reach for `extra_lenses`
    when you want something the nine built-ins don't already cover, like the
    house-style rule above.

## As reusable skill files

To share lenses across repos — or to let an agent harness drop its own lens in —
put each one in its own file and point `lens_paths` at the file or a directory:

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

A skill file may hold one lens (a mapping) or several (a list). Lenses loaded from
`lens_paths` are appended to any inline `extra_lenses`; `id`s must be unique across
the whole set.

## Bundled lens packs (`pack:<name>`)

lgtmaybe ships a curated, **opt-in** library of extra lenses — distilled from the
wider engineering-review ecosystem (Ousterhout, Metz, Carmack, the NASA Power of
Ten / TigerStyle, Google's eng-practices, and several skill/rule collections; see
[ATTRIBUTION.md](https://github.com/MattJColes/lgtmaybe/blob/main/ATTRIBUTION.md)).
They are **off by default** — they are more opinionated than the nine built-ins,
and every lens you enable is another model call per review. Enable a pack by name
with the `pack:` scheme in `lens_paths` (this works for a `pip install`, with no
repo-relative path to point at):

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
| `pack:design` | `wrong-abstraction`, `shallow-module`, `information-leakage`, `errors-out-of-existence`, `hidden-state`, `naming` | You want taste/structure review (the "right abstraction", deep modules, explicit state). |
| `pack:robustness` | `assertions`, `bounded`, `idempotency`, `migrations`, `portability`, `observability` | Operational safety: defensive invariants, bounded work, retry-safe side effects, safe migrations. |
| `pack:interface` | `api-design`, `type-safety`, `magic-values`, `comment-why` | Contracts & clarity: backward compatibility, tight types, named constants, why-not-what comments. |
| `pack:frontend` | `accessibility`, `i18n` | The diff touches UI / user-facing copy. |

Each pack is a directory of skill files under the package, so you can also browse
or copy any single lens from
[`src/lgtmaybe/lenses/`](https://github.com/MattJColes/lgtmaybe/tree/main/src/lgtmaybe/lenses).
An unknown `pack:` name fails loudly, listing the packs that exist.

## Run it

Custom lenses run automatically on every review — CLI or GitHub Action — once they
are in your config. On the CLI you'll see findings titled by your lens just like
the built-ins:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b --api-base http://localhost:11434
```

To narrow a run to fewer **built-in** lenses while keeping your custom ones, set
`categories` — the two lists are independent.

## Security: lenses are trusted input

A lens's `instructions` and example go straight into the model's system prompt, so
treat a lens like code you run: **only define lenses in files you control.** Keep
them in your committed `.lgtmaybe.yml` or repo skill files, never source them from
a pull request's contents. On `pull_request_target` the Action reads config from
the base repository, not the PR head, so a fork PR cannot introduce or alter a
lens. Diff content itself is always treated as untrusted data, separately from
your lenses.

## See also

- [Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md#extra_lenses) — the field reference.
- [What gets reviewed](../explanation/what-gets-reviewed.md) — the built-in lenses.
