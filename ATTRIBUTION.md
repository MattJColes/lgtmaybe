# Inspired by

lgtmaybe's review lenses distill widely-shared engineering wisdom and the
broader "skills / rules / review-prompt" ecosystem into focused, structured
lenses. This page credits those sources so the project can be an honest FOSS
one-stop shop rather than a silent re-implementation.

Two honesty notes up front:

- These are **concept attributions**. A lens is our own wording of an idea; it is
  not copied text. Where the idea comes from a book or essay (Ousterhout, Metz,
  Carmack) the source is the *concept*, not a review-skill file.
- A few sources below were **cross-checked across multiple references rather than
  fetched directly** (some hosts block automated fetches). Where we could not
  trace an idea to any specific published source, the lens is marked an **original
  synthesis** and cites no repo — we would rather say "ours" than invent a lineage.

## Built-in lens

| Lens | Idea / source | Where |
|---|---|---|
| `ponytail` | **Ponytail** — "the laziest senior dev in the room; the best code is the code you never wrote." | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) |

## Bundled opt-in packs (`pack:design`, `pack:robustness`, `pack:interface`, `pack:frontend`)

| Lens | Idea / source | Where |
|---|---|---|
| `wrong-abstraction` | Sandi Metz — **"The Wrong Abstraction"** (duplication is cheaper than the wrong abstraction) and **Sandi Metz' Rules**. | [sandimetz.com](https://sandimetz.com/blog/2016/1/20/the-wrong-abstraction) · [thoughtbot](https://thoughtbot.com/blog/sandi-metz-rules-for-developers) |
| `shallow-module` | John Ousterhout — **A Philosophy of Software Design** (deep vs. shallow modules; pass-through methods; temporal decomposition). | [aposd (Stanford)](https://web.stanford.edu/~ouster/cgi-bin/aposd.php) |
| `information-leakage` | John Ousterhout — *A Philosophy of Software Design* (information leakage: one design decision known in two places). | [aposd (Stanford)](https://web.stanford.edu/~ouster/cgi-bin/aposd.php) |
| `errors-out-of-existence` | John Ousterhout — *A Philosophy of Software Design* ("define errors out of existence"). | [aposd (Stanford)](https://web.stanford.edu/~ouster/cgi-bin/aposd.php) |
| `hidden-state` | John Carmack — **"Inlined Code" / functional-style** argument (make all touched state explicit; mutation is the enemy). | [number-none.com](http://number-none.com/blow/blog/programming/2014/09/26/carmack-on-inlined-code.html) |
| `naming` | John Ousterhout — *A Philosophy of Software Design* (choosing names); the naming chapter of **Clean Code**. | [aposd (Stanford)](https://web.stanford.edu/~ouster/cgi-bin/aposd.php) |
| `assertions` | **The Power of 10** (Holzmann, NASA/JPL) — assertion density; and **TigerStyle** (TigerBeetle) — assert the positive *and* negative space. | [P10 (PDF)](https://spinroot.com/gerard/pdf/P10.pdf) · [TIGER_STYLE.md](https://github.com/tigerbeetle/tigerbeetle/blob/main/docs/TIGER_STYLE.md) |
| `bounded` | **The Power of 10** (bound all loops) and **TigerStyle** ("bound everything"). | [P10 (PDF)](https://spinroot.com/gerard/pdf/P10.pdf) · [TIGER_STYLE.md](https://github.com/tigerbeetle/tigerbeetle/blob/main/docs/TIGER_STYLE.md) |
| `api-design` | **qodo-ai/pr-agent** reviewer prompt ("might break other code") and **github/awesome-copilot** generic code-review instructions (breaking changes). | [pr-agent](https://github.com/qodo-ai/pr-agent) · [awesome-copilot](https://github.com/github/awesome-copilot) |
| `type-safety` | Alexis King — **"Parse, don't validate"**; Yaron Minsky — **"Make illegal states unrepresentable"**. | [lexi-lambda](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/) · [Jane Street](https://blog.janestreet.com/effective-ml-revisited/) |
| `magic-values` | Martin Fowler — *Refactoring* (**Replace Magic Literal**); github/awesome-copilot generic review (magic numbers). | [refactoring.com](https://refactoring.com/catalog/replaceMagicLiteral.html) · [awesome-copilot](https://github.com/github/awesome-copilot) |
| `comment-why` | Google — **Engineering Practices / Code Review** ("comments should explain *why*, not *what*"); Ousterhout. | [google.github.io/eng-practices](https://google.github.io/eng-practices/review/reviewer/looking-for.html) |
| `observability` | github/awesome-copilot **security-and-owasp** (logging quality) and structured-logging practice. Excludes secrets/PII-in-logs (that stays in the built-in `security` lens). | [awesome-copilot](https://github.com/github/awesome-copilot) |
| `accessibility` | **eslint-plugin-jsx-a11y** (concrete a11y rule classes) and github/awesome-copilot accessibility instructions. | [eslint-plugin-jsx-a11y](https://github.com/jsx-eslint/eslint-plugin-jsx-a11y) · [awesome-copilot](https://github.com/github/awesome-copilot) |
| `idempotency` | **Original synthesis** from distributed-systems practice (at-least-once delivery; retry-safety). No single review-skill source; for the concept, see Stripe's idempotency-keys design. | [docs.stripe.com](https://docs.stripe.com/api/idempotent_requests) |
| `migrations` | **Original synthesis** from online-schema-change practice (expand/contract; lock-safe DDL). No single review-skill source; for the concept, see `strong_migrations`. | [ankane/strong_migrations](https://github.com/ankane/strong_migrations) |
| `i18n` | **Original synthesis** from general localization practice (externalized strings; locale-safe formatting; Unicode handling). | [cldr.unicode.org](https://cldr.unicode.org/) |
| `portability` | **Original synthesis** from cross-platform practice (POSIX, `pathlib`/temp-dir APIs, no hardcoded paths). | — |

## Bundled semgrep rules

`src/lgtmaybe/rules/semgrep/` is **our own work, MIT-licensed** — not a vendored
copy of anyone's pack.

That is deliberate. The obvious move would be to bundle a curated subset of
[semgrep-rules](https://github.com/semgrep/semgrep-rules) (or its
[opengrep-rules](https://github.com/opengrep/opengrep-rules) fork), which is
what most tools do. Both are **LGPL-2.1 plus a Commons Clause** condition, and
the Commons Clause is not an open-source licence: it forbids providing "a
product or service whose value derives, entirely or substantially, from the
functionality of the Software". Shipping that inside an MIT wheel published to
PyPI, Homebrew and GHCR would misrepresent what users are getting and restrict
them in ways MIT promises it does not.

So the bundled pack is small and ours. It is not a replacement for those
collections — point `static_analysis.semgrep_rules` at a fuller pack you have
obtained yourself if you want their coverage and accept their terms.

## Ecosystems scanned

These collections were surveyed while curating the packs; ideas already covered by
the nine built-ins (OWASP security, correctness, deprecated/EOL deps, weak tests,
stale docs, N+1/quadratic performance, nesting/duplication, intent, YAGNI) were
deliberately **not** duplicated as new lenses:

- [github/awesome-copilot](https://github.com/github/awesome-copilot) — Copilot custom instructions.
- [baz-scm/awesome-reviewers](https://github.com/baz-scm/awesome-reviewers) — review prompts distilled from real maintainer feedback.
- [qodo-ai/pr-agent](https://github.com/qodo-ai/pr-agent) — PR-Agent reviewer prompts.
- [trailofbits/skills](https://github.com/trailofbits/skills) — security review skills (footgun/insecure-defaults framing).
- [obra/superpowers](https://github.com/obra/superpowers) — engineering skills (testing anti-patterns, code-review etiquette).
- [anthropics/skills](https://github.com/anthropics/skills) — official Agent Skills.

If you maintain one of these and want the attribution worded differently — or
removed — open an issue.
