---
description: Post a C4-style Mermaid diagram of a pull request's changes as a GitHub comment, or print one from the CLI.
---

# Generate a change diagram

lgtmaybe can post a **C4-style diagram of what a pull request changes** — the
containers and components the PR touches plus their immediate relationships — so
a reviewer gets a visual overview before they read the diff. It's a separate
concern from the review and the description: enable any of them independently.

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

Changed elements are marked in the labels (`(new)` / `(changed)`), and the
diagram stays honest about the diff being only a slice of the codebase —
relationships it infers from imports rather than the diff itself are called out
in the notes rather than asserted as fact.

## On GitHub: `/diagram` and `auto_diagram`

Comment **`/diagram`** on a pull request to post (or update in place) the change
diagram. Like `/describe`, it's an idempotent upsert — re-running edits the same
comment instead of stacking new ones.

To post it automatically when a PR is opened, set the `auto_diagram` Action
input (off by default; it fires only on `opened` / `reopened`, never on a
`synchronize` push):

```yaml
      - uses: MattJColes/lgtmaybe@v1
        with:
          provider: anthropic
          model: claude-sonnet-4-5
          auto_diagram: "true"
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

You can also set it in `.lgtmaybe.yml`:

```yaml
auto_diagram: true
```

The diagram is best-effort — a failure is logged and never blocks the review.

## Locally: `lgtmaybe diagram`

`lgtmaybe diagram` prints a diagram of your local changes — no GitHub involved:

```console
$ lgtmaybe diagram --provider ollama --model llama3
```

It diffs your branch against the base (the same base resolution as `lgtmaybe
review`; `--base` overrides, `--working` includes uncommitted edits,
`--uncommitted` reviews only the working-tree edits). The output is the ASCII
rendering (which renders in your terminal) followed by the Mermaid source —
paste that into a GitHub comment, [mermaid.live](https://mermaid.live), or a
Markdown file to render it.

## Why Mermaid (and what the ASCII is for)

GitHub renders **Mermaid** natively in comments and Markdown, and Mermaid has a
C4 dialect — so a `mermaid` fenced block renders in the comment with no image to
generate or host. That matters for a `pull_request_target` reviewer: hosting an
image would mean committing a file or calling an external service, neither of
which fits a fork-safe, idempotently-updated comment.

A terminal, though, can't render Mermaid — which is exactly why the same call
also returns **ASCII art**. The CLI prints the ASCII; the GitHub comment shows
the Mermaid with the ASCII tucked in a collapsible "Text version", which also
means a reviewer never sees a red "unable to render" box if a diagram comes back
malformed.

D2 (the [C4 notation](https://d2lang.com/blog/c4/) that inspired this) isn't
used: GitHub doesn't render D2 in Markdown, so it would show as source anyway.

## See also

- [Configure `.lgtmaybe.yml`](configure-lgtmaybe-yml.md)
- [Use as a GitHub Action](use-as-github-action.md)
- [Configuration reference](../reference/config.md)
