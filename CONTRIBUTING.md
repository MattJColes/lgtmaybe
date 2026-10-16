# Contributing to lgtmaybe

Thanks for contributing. lgtmaybe is maintained by one person, so keep each
change focused and open a PR. Code changes need a test; documentation changes
need a clean docs build.

## The Bar

A code PR needs **green CI and a behavioural test**. A documentation-only PR
needs `mkdocs build --strict` to pass (CI builds the docs on every pull request that
touches them)
and the readability checks in `tests/docs` to pass. Follow the style guide in
[`docs/how-to/write-docs.md`](docs/how-to/write-docs.md): write for an engineer
new to the project, keep sentences short, and link project terms to the
[glossary](docs/reference/glossary.md). Specifically:

- **Write the test first for code changes.** Start from the expected input and
  output, watch the test fail, then write the minimum code to pass. A code
  change without a test will not be merged.
- **Tests are behavioural.** Call the function, assert the result. Use the
  fakes in `tests/fakes/`, and only mock at true system boundaries (the LLM, the
  code host's API). Don't mock the code under test.
- **Scope is the gate.** lgtmaybe does one thing: review PRs (`fetch → compress
  → prompt → parse → post`). Out-of-scope PRs (auto-merge, auto-fix, changelog
  generation, chat integrations, …) are declined regardless of quality. If a
  change is large or speculative, open an issue first.

The decisions that are *made, not options* live in [`CLAUDE.md`](CLAUDE.md).
Read it before a non-trivial change: structured output only, fork safety, no
static cloud keys, ports frozen in `core/ports.py`.
[`DEVELOPMENT.md`](DEVELOPMENT.md) covers running the CLI locally and the test
layout.

## Local Setup

The project uses [uv](https://github.com/astral-sh/uv).

```bash
uv sync --dev
```

Run exactly what CI runs (`.github/workflows/ci.yml`, on Ubuntu and Windows):

```bash
uv lock --check              # lockfile matches pyproject (no drift)
uv run ruff check .          # lint
uv run ruff format --check . # format (omit --check to auto-format)
uv run mypy                  # types (strict)
uv run pytest -q             # tests
```

`pytest` treats `DeprecationWarning`, `PendingDeprecationWarning` and
`EncodingWarning` as errors, so using a deprecated API fails the suite. Fix the call, or, if the warning comes
from a third-party library we don't control, add a narrow `ignore` to
`filterwarnings` in `pyproject.toml`. Outdated-version and CVE checks run **in
the background** (Dependabot and the `audit` workflow), not in this per-PR gate:
they depend on what's published upstream, so they can't be deterministic.

If you change `ReviewConfig` or any docs page, regenerate the generated files
(`docs/reference/config.md`, `docs/llms.txt`, `docs/llms-full.txt`) as described
in [DEVELOPMENT.md](DEVELOPMENT.md#regenerating-generated-files); the test suite
fails when they are stale.

Build the docs (or replace `build --strict` with `serve` for a live preview).
CI only builds them after merge, so run this yourself for documentation changes:

```bash
uv run --group docs mkdocs build --strict
```

## Opening a PR

1. Branch with a descriptive prefix such as `feat/`, `fix/`, `chore/` or
   `docs/`.
2. Write [conventional commit](https://www.conventionalcommits.org/) messages
   (`feat: …`, `fix: …`, `docs: …`). The `commitlint` workflow checks every
   commit in the PR, because release-please reads them to choose the next
   version and write the changelog.
3. For code changes, work test-first, and keep the PR focused.
4. Run the checks above; build the docs for documentation changes.
5. Open the PR with a short description of the change. The maintainer dogfoods
   lgtmaybe on its own PRs, so expect an automated review too. On a branch in
   this repository (not a fork), the `lint-fix` workflow also pushes a commit
   when ruff can fix something automatically.

## Good First Issues

Look for the `good first issue` label. Natural self-contained starters:

- **A new provider adapter**: litellm already normalises the call, so most of
  the work is the factory mapping, credential resolution, and a test.
- **A how-to guide**: a task recipe under `docs/how-to/`.
- **A test fixture**: a realistic diff under `tests/github/fixtures/` that
  exercises an edge case.

## Licensing

lgtmaybe is MIT-licensed. There is no CLA: by contributing you agree your
contribution is licensed under the same MIT terms (inbound = outbound).
