# Contributing to lgtmaybe

Thanks for contributing. lgtmaybe is maintained by one person, so keep each
change focused and open a PR. Code changes need a test; documentation changes
need a successful docs build.

## The bar

A code PR needs **green CI and a behavioral test**. Documentation-only PRs need
the docs build to pass. Specifically:

- **Write the test first for code changes.** Start from the expected input and
  output, watch the test fail, then write the minimum code to pass. CI rejects
  code changes without a test.
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

Build the docs (or replace `build --strict` with `serve` for a live preview):

```bash
uv run --group docs mkdocs build --strict
```

## Opening a PR

1. Branch with a conventional prefix: `feat/`, `fix/`, `chore/`, `docs/`.
2. For code changes, make the change test-first. Keep every PR focused.
3. Run the relevant checks above; build the docs for documentation changes.
4. Open the PR with a short description of the change. The maintainer
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
