# Add a custom review lens (BYO skills)

lgtmaybe ships eight built-in review lenses (security, correctness, deprecation,
tests, documentation, performance, complexity, intent). A **custom lens** lets you
add your own — a "skill file" that runs alongside the built-ins, fans out as its
own focused model call, and merges its findings into the same review. Use it to
bake in a house style, a senior-dev instinct, or a team convention the built-in
lenses don't cover.

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
  - id: simplify
    title: Simplify or delete
    instructions: |
      Channel the laziest senior dev in the room: the best code is the code you
      never wrote. Before accepting new code, ask whether it needs to exist at
      all. Flag needless wrappers, premature abstraction, re-implementing the
      standard library, and "just in case" code with no caller. Prefer one line
      over ten.
    example_diff: |
      --- a/util.py
      +++ b/util.py
      @@ -4,1 +4,3 @@
       def get_name(user):
      +    name = user.name
      +    return name
    example_finding:
      path: util.py
      line: 5
      severity: low
      title: Needless local variable
      body: The temporary adds nothing; return user.name directly.
      suggestion: "    return user.name"
```

That single lens is the spirit of [Ponytail](https://github.com/DietrichGebert/ponytail)
expressed as an lgtmaybe lens.

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
# .lgtmaybe/skills/simplify.yml
id: simplify
title: Simplify or delete
instructions: |
  The best code is the code you never wrote. Flag needless wrappers, premature
  abstraction, and code with no caller.
```

A skill file may hold one lens (a mapping) or several (a list). Lenses loaded from
`lens_paths` are appended to any inline `extra_lenses`; `id`s must be unique across
the whole set.

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
- [FOSS and the future](../explanation/foss-and-the-future.md) — where BYO lenses fit the roadmap.
