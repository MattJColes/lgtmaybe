---
description: Run your first lgtmaybe pull-request review locally with ollama — no API keys, no GitHub token, and no pull request required.
---

# Getting Started with lgtmaybe

This tutorial walks you through your first review using **ollama** — a fully
local model that costs nothing and needs no API keys. By the end you will have
reviewed a branch and seen the findings in your terminal, with no GitHub token
and no pull request required.

## What you need

- Python 3.11 or later
- [ollama](https://ollama.com) running locally
- A local git repository with some changes on a branch to review

## Step 1 — Install lgtmaybe

```bash
pip install lgtmaybe
```

On macOS you can install from the Homebrew tap instead:

```bash
brew tap MattJColes/lgtmaybe
brew trust MattJColes/lgtmaybe   # current Homebrew requires trusting third-party taps
brew install lgtmaybe
```

See [Install with Homebrew](../how-to/install-with-homebrew.md) for details (the
`brew trust` step, and that the Homebrew build covers the API-key and local
providers while keyless cloud providers need the `pip` extras).

Verify the install:

```bash
lgtmaybe --help
```

## Step 2 — Start ollama and pull a model

```bash
ollama serve          # starts the local server on http://localhost:11434
ollama pull qwen3.6:27b    # or any model you prefer
```

Leave `ollama serve` running in a separate terminal.

## Step 3 — Review your changes

From inside a git repo, on a branch with some changes, run:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

lgtmaybe diffs your current branch against the remote primary branch
(`origin/HEAD`, falling back to `origin/main`), sends the changed lines to your
local qwen3.6:27b instance, and prints the findings to your terminal:

```console
src/app.py:2  [MEDIUM] Import order
  sys should be sorted before os

1 finding · provider ollama · model qwen3.6:27b
```

To review the whole worktree — your branch's commits plus uncommitted edits —
add `--working`; for only the uncommitted edits, add `--uncommitted`; to diff
against a different base, pass `--base main`.

## Step 4 — Change the output format

`--format` controls what `review` prints. `--json` (shorthand for
`--format json`) emits a JSON array ready to pipe into other tooling:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b \
  --api-base http://localhost:11434 --json
```

`--format agent` instead prints the findings as correction instructions an AI
coding agent can read and apply, for a local review-and-fix loop — see
[Fix findings with an AI agent](../how-to/fix-findings-with-an-ai-agent.md).

## Step 5 — Post reviews on real pull requests

The CLI reviews local changes. To run lgtmaybe on actual pull requests — inline
comments and a summary posted back to GitHub — add the GitHub Action to your
repo. See [Use as a GitHub Action](../how-to/use-as-github-action.md).

## What happened under the hood

lgtmaybe ran its pipeline over your local diff:

1. **fetch** — read the diff from your local repo with `git diff`
2. **compress** — stripped generated files, binaries, and lockfiles
3. **prompt** — built a structured prompt asking for JSON output
4. **parse** — validated the model's JSON against the `ReviewFinding` schema
5. **render** — printed the findings (the Action posts them to the PR instead)

## Next steps

- To configure severity thresholds, path filters, and token caps, see
  [Configure .lgtmaybe.yml](../how-to/configure-lgtmaybe-yml.md).
- To use a cloud provider with no API keys, see
  [Review with Bedrock OIDC](../how-to/review-with-bedrock-oidc.md) or
  [Review with Vertex WIF](../how-to/review-with-vertex-wif.md).
- To use lgtmaybe in a GitHub Actions workflow, see
  [Use as a GitHub Action](../how-to/use-as-github-action.md).
