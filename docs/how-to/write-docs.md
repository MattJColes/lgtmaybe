---
description: How to write lgtmaybe's docs so an engineer new to the project can follow them, and the checks that keep them that way.
---

# Write the Docs

These docs are for every engineer, including one who has never used lgtmaybe
and is new to code review tools. Write so that the reader can follow along the
first time, without a second tab open to look things up.

## The Reader

Picture an engineer who knows how to use git and a terminal, but not this
project. They do not know our words for things yet. They want to get something
working, then understand why it works.

## How to Write

- **Put one idea in each paragraph.** If a paragraph needs a second idea, it
  needs a second paragraph.
- **Keep sentences short.** Aim for under 25 words. Split a sentence that has
  more than one "and", "which" or "because" in it.
- **Explain a term the first time you use it,** or link it to the
  [glossary](../reference/glossary.md). Project words such as
  "[lens](../reference/glossary.md#lens)",
  "[preset](../reference/glossary.md#preset)" and
  "[watermark](../reference/glossary.md#watermark)" must link to their glossary
  entry on every page that uses them.
- **Show before you explain.** Give the command or the config first, then say
  what it does and why.
- **Use the active voice.** Write "lgtmaybe posts a comment", not "a comment is
  posted".
- **Use a list for anything that is a list.** Three or more items in a sentence
  usually read better as bullets.
- **Keep facts exact.** Never change a config key, a default value, a command or
  a code example to make a sentence shorter. Shorter wording, never fewer facts.
- **Say what the reader will see.** "The run fails with a message naming the
  missing key" beats "an error occurs".

## House Style

- Headings use Title Case.
- Spelling is Australian English: "behaviour", "colour", "organise",
  "artefact".
- Write lgtmaybe and ollama in lowercase, even at the start of a sentence.
- Use " - " or a full stop, never an em dash.
- Skip filler such as "it's worth noting", "simply" and "just". Skip "not only X
  but also Y" and lists of three padded to sound complete.
- Do not end a section by restating what it said.

## What the Checks Enforce

`tests/docs/test_readability.py` reads each page's prose (not its code, tables
or headings) and fails when a page has:

- an average sentence over 18 words;
- any sentence over 35 words;
- an em dash;
- a key project term with no link to its glossary entry.

Run the checker yourself to see every problem on a page, with the sentence that
caused it:

```bash
uv run python scripts/doc_readability.py docs/how-to/your-page.md
```

Run it with no arguments for a summary of every page. Pages that do not pass yet
are listed in `NOT_YET_PASSING` in the test. That list can only shrink: when you
fix a page, the test fails until you take it off the list.

## Glossary and Tooltips

- Add a term to the [glossary](../reference/glossary.md) when a page needs it
  and it is not there yet. Keep the entry to two or three short sentences, and
  link to the page where the term matters.
- Acronyms such as OIDC and SARIF also get a hover tooltip on every page. Add
  one line to `includes/abbreviations.md`, and give it a glossary entry too.

## Before You Open a Pull Request

1. Run `uv run python docs/generate_llms_txt.py` so `docs/llms.txt` and
   `docs/llms-full.txt` match the pages.
2. Run `uv run --group docs mkdocs build --strict`. It also fails on a link to a
   heading that no longer exists.
3. Run `uv run pytest tests/docs -q`.
