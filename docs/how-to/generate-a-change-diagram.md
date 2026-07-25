---
description: Post a C4-style Mermaid diagram of a pull request's changes as a GitHub comment, or print one from the CLI.
---

# Generate a change diagram

lgtmaybe can post a **C4-style diagram of what a pull request changes** — the
containers and components the PR touches plus their immediate relationships — so
a reviewer gets a visual overview before they read the diff. It's a separate
concern from the review and the description: enable any of them independently.

![A /diagram comment posted by lgtmaybe: a C4 diagram of a Stripe webhook change, with the changed Checkout API and new Orders DB and Billing worker components highlighted with green borders](../assets/marketplace/marketplace-screenshot-4.png){ width="720" }

## Contents

- [What you get](#what-you-get)
- [On GitHub: `/diagram` and `auto_diagram`](#on-github-diagram-and-auto_diagram)
- [Locally: `lgtmaybe diagram`](#locally-lgtmaybe-diagram)
- [Why Mermaid (and what the ASCII is for)](#why-mermaid-and-what-the-ascii-is-for)
- [See also](#see-also)

## What you get

One model call returns two renderings of the same graph:

- a **Mermaid C4 diagram** (`C4Container` / `C4Context`), which GitHub renders
  natively inside the comment — no image, no external hosting; and
- a **plain-text ASCII** rendering of the same diagram, which is what shows in a
  terminal and serves as the fallback if the Mermaid can't be rendered.

Changed elements are marked in the labels (`(new)` / `(changed)`) **and get a
green border**, so the PR's footprint stands out from the untouched
collaborators at a glance; the relationship lines are restyled in a mid-grey
that stays readable on every GitHub theme — Mermaid's C4 renderer defaults
them to a near-black that disappears in dark mode — and the layout is loosened
to three cards per row so the renderer's fixed-width cards don't overlap. The diagram also stays
honest about the diff being only a slice of the codebase —
relationships it infers from imports rather than the diff itself are called out
in the notes rather than asserted as fact.

Here is what the posted comment looks like for a PR that puts a Redis cache in
front of a user service — the Mermaid renders in place, with the ASCII tucked
in a collapsible "Text version" underneath:

> **Cache user lookups in Redis**

```mermaid
C4Container
    title User lookup after this change
    Person(client, "Client")
    Container(api, "User API", "Python", "Serves user reads")
    ContainerDb(cache, "Redis cache", "Redis", "caches user rows (new)")
    ContainerDb(db, "User DB", "Postgres")
    Rel(client, api, "GET /users/{id}")
    Rel(api, cache, "check cache (new)")
    Rel(api, db, "on miss, query")
    UpdateRelStyle(client, api, $textColor="#808080", $lineColor="#808080")
    UpdateRelStyle(api, cache, $textColor="#808080", $lineColor="#808080")
    UpdateRelStyle(api, db, $textColor="#808080", $lineColor="#808080")
    UpdateElementStyle(cache, $borderColor="#54d090")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

<details>
<summary>Text version</summary>

```
[Client] --> [User API] --check--> [Redis cache] (new)
                  |
                  +--miss--> [User DB]
```

</details>

> *The User DB link is inferred from an import, not shown in the diff.*

## On GitHub: `/diagram` and `auto_diagram`

Comment **`/diagram`** on a pull request to post (or update in place) the change
diagram. Like `/describe`, it's an idempotent upsert — re-running edits the same
comment instead of stacking new ones.

The supplied starter workflows set `auto_diagram: true`, so new repositories
post one automatically when a PR is opened or reopened. The underlying Action
input remains off by default for backwards compatibility, and it never fires
on a `synchronize` push. To enable it in an existing workflow:

```yaml
      - uses: MattJColes/lgtmaybe@v1
        with:
          provider: anthropic
          model: claude-sonnet-4-6
          auto_diagram: "true"
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

You can also set it in `.lgtmaybe.yml`:

```yaml
auto_diagram: true
```

The diagram is best-effort — a failure is logged and never blocks the review.
To opt out of the starter-workflow default, remove `auto_diagram` or set it to
`false`.

## Locally: `lgtmaybe diagram`

`lgtmaybe diagram` prints a diagram of your local changes — no GitHub involved:

```console
$ lgtmaybe diagram --provider ollama --model llama3
```

It diffs your branch against the base (the same base resolution as `lgtmaybe
review`; `--base` overrides, `--working` includes uncommitted edits,
`--uncommitted` reviews only the working-tree edits). The output is the same
Markdown body the `/diagram` comment carries — the Mermaid source first, then
the ASCII rendering (which reads fine in a terminal) in a collapsed "Text
version" block. Paste the Mermaid into a GitHub comment,
[mermaid.live](https://mermaid.live), or a Markdown file to render it.

## Why Mermaid (and what the ASCII is for)

GitHub renders **Mermaid** natively in comments and Markdown, and Mermaid has a
C4 dialect — so a `mermaid` fenced block renders in the comment with no image to
generate or host. That matters for a `pull_request_target` reviewer: hosting an
image would mean committing a file or calling an external service, neither of
which fits a fork-safe, idempotently-updated comment.

A terminal, though, can't render Mermaid — which is exactly why the same call
also returns **ASCII art**. Both the CLI output and the GitHub comment show the
Mermaid with the ASCII tucked in a collapsible "Text version" — the ASCII is
what you actually read in a terminal, and it doubles as the fallback body, so a
reviewer never sees a red "unable to render" box if a diagram comes back
malformed.

D2 (the [C4 notation](https://d2lang.com/blog/c4/) that inspired this) isn't
used: GitHub doesn't render D2 in Markdown, so it would show as source anyway.

## See also

- [Configure `.lgtmaybe.yml`](configure-lgtmaybe-yml.md)
- [Use as a GitHub Action](use-as-github-action.md)
- [Configuration reference](../reference/config.md)
