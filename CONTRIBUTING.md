# Contributing to lgtmaybe

Thanks for considering a contribution. lgtmaybe is solo-maintained — the
maintainer merges and decides — so this is deliberately short: run it locally,
add a test, open a PR.

## The bar

A PR is mergeable when it has **green CI and a test** — not when it wins a style
debate (ruff handles style). Specifically:

- **TDD, always.** Write the test from the change's stated input/output *first*,
  watch it fail, then write the minimum code to pass. CI rejects a diff that adds
  code without a test.
- **Tests are behavioural.** Call the function, assert the result. Use the fakes
  in `tests/fakes/`; only mock at true system boundaries (the LLM, the GitHub
  API). Don't mock the code under test.
- **Scope is the gate.** lgtmaybe does one thing: review PRs (`fetch → compress →
  prompt → parse → post`). Out-of-scope PRs (auto-merge, auto-fix, changelog
  generation, chat integrations, …) are declined regardless of quality. If a
  change is large or speculative, open an issue first.

The decisions that are *made, not options* live in [`CLAUDE.md`](CLAUDE.md) —
read it before a non-trivial change (structured output only, fork safety, no
static cloud keys, ports frozen in `core/ports.py`).

## Local setup

The project uses [uv](https://github.com/astral-sh/uv).

```bash
uv sync --dev
```

Run exactly what CI runs:

```bash
uv lock --check              # lockfile matches pyproject (no drift)
uv run ruff check .          # lint
uv run ruff format --check . # format (omit --check to auto-format)
uv run mypy                  # types (strict)
uv run pytest -q             # tests
```

`pytest` treats `DeprecationWarning` as an error, so using a deprecated API
fails the suite — fix the call, or, if it comes from a third-party library we
don't control, add a narrow `ignore` to `filterwarnings` in `pyproject.toml`.
Outdated-version and CVE checks run **in the background** (Dependabot and the
`audit` workflow), not in this per-PR gate — they depend on what's published
upstream, so they can't be deterministic.

Preview the docs site locally:

```bash
uv run --group docs mkdocs serve
```

## Opening a PR

1. Branch with a conventional prefix: `feat/`, `fix/`, `chore/`, `docs/`.
2. Make the change test-first; keep it minimal and focused.
3. Ensure the five commands above are green.
4. Open the PR with a short description of the behaviour change. The maintainer
   dogfoods lgtmaybe on its own PRs, so expect an automated review too.

## Good first issues

Look for the `good first issue` label. Naturally self-contained starters:

- **A new provider adapter** — litellm already normalises the call; most of the
  work is the factory mapping, credential resolution, and a test.
- **A how-to guide** — a task recipe under `docs/how-to/`.
- **A test fixture** — a realistic diff under `tests/github/fixtures/` that
  exercises an edge case.

## Licensing

lgtmaybe is MIT. There is no CLA — by contributing you agree your contribution is
licensed under the same MIT terms (inbound = outbound).
