---
description: Run your first lgtmaybe review locally with ollama, with no API keys, no GitHub token, and no pull request.
---

# Getting Started with lgtmaybe

This tutorial walks you through your first review using **ollama**, which runs
the model on your own machine, costs nothing, and needs no API keys. By the end
you will have reviewed a branch and seen the findings in your terminal, with no
GitHub token and no pull request.

## What You Need

- Python 3.11 or later
- [ollama](https://ollama.com) running locally
- A local git repository with some changes on a branch to review

## Step 1: Install lgtmaybe

```bash
pip install lgtmaybe
```

On macOS you can install from the Homebrew tap instead:

```bash
brew tap MattJColes/tap
brew trust MattJColes/tap   # current Homebrew requires trusting third-party taps
brew install lgtmaybe
```

[Install the CLI](../how-to/install-the-cli.md) explains the `brew trust` step,
the Windows (WinGet) install, and which providers the base install covers.
API-key and local providers work out of the box; keyless cloud providers need a
`pip` extra.

Verify the install:

```bash
lgtmaybe --help
```

## Step 2: Start ollama and Pull a Model

```bash
ollama serve          # starts the local server on http://localhost:11434
ollama pull qwen3.6:27b   # or any model you prefer
```

Leave `ollama serve` running in a separate terminal.

## Step 3: Review Your Changes

From inside a git repo, on a branch with some changes, run:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

lgtmaybe diffs your current branch against the remote primary branch
(`origin/HEAD`, falling back to `origin/main` / `origin/master`, then a local
`main` / `master`). It sends the diff, with some surrounding lines for context,
to your local qwen3.6:27b model and prints the findings to your terminal:

```console
src/app.py:14  [MEDIUM] Missing None check before use (confidence 8/10)
  find_user() returns None for an unknown id, so user.email raises AttributeError.

1 finding · risk low (no risk signals) · provider ollama · model qwen3.6:27b · lgtmaybe 2.11.1
```

Each finding shows its file and line, severity, and title, then the
explanation. The last line counts the findings, rates the risk of the change,
and names the model and lgtmaybe version.

To review the whole worktree (your branch's commits plus uncommitted edits),
add `--working`. To review only the uncommitted edits, add `--uncommitted`. To
diff against a different base, pass `--base main`.

## Step 4: Change the Output Format

`--format` controls what `review` prints. `--json` (shorthand for
`--format json`) emits a JSON array ready to pipe into other tooling:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b \
  --api-base http://localhost:11434 --json
```

`--format agent` prints the findings as correction instructions an AI coding
agent can read and apply, for a local review-and-fix loop. See
[Fix findings with an AI agent](../how-to/fix-findings-with-an-ai-agent.md).

## Step 5: See the Whole Change

`lgtmaybe diagram` sends the same local diff to three concurrent model calls and
prints the **change overview**:

- a description of what you changed
- a risk-of-change rating (computed locally, no model call)
- a **High Impact Areas** section calling out anything that could bite, such as
  infrastructure, security posture, outage risk, migrations, or backups
- a diagram of the components your change touches, plus a sequence diagram
  when it alters a run-time flow

```bash
lgtmaybe diagram \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

It takes the same `--base` / `--working` / `--uncommitted` flags as `review`.
Run `review` then `diagram` before you open a pull request: the first tells you
what's wrong with the change, the second what the change is and what it could
break. The diagrams print as Mermaid source plus a text rendering for the
terminal. Paste the Mermaid into a GitHub comment or
[mermaid.live](https://mermaid.live) to see it drawn. See
[Generate a change overview](../how-to/generate-a-change-diagram.md).

## Step 6: Post Reviews on Real Pull Requests

The CLI reviews local changes. To post inline comments and a summary on real
pull requests, wire lgtmaybe into your code host. The review is the same on all
three; only the setup differs:

- **GitHub:** add the Action to your repo.
  See [Use as a GitHub Action](../how-to/use-as-github-action.md).
- **GitLab:** add a CI job.
  See [Review on GitLab](../how-to/review-on-gitlab.md).
- **Gitea:** add a Gitea Actions workflow.
  See [Review on Gitea](../how-to/review-on-gitea.md).

## What Happened Under the Hood

lgtmaybe ran its pipeline over your local diff:

1. **Fetch:** read the diff from your local repo with `git`.
2. **Filter:** skipped generated files, binaries, and lockfiles, and redacted
   anything that looked like a secret.
3. **Review:** sent four concurrent model calls (security, correctness, code
   health, and tests/documentation), each asking for findings as JSON.
4. **Parse and anchor:** validated each reply against the findings schema and
   pinned every finding to the changed line it quotes.
5. **Reflect:** asked the model to audit its own findings and dropped the ones
   it could not stand behind.
6. **Filter findings:** dropped defect findings with no concrete failure
   scenario and anything below the severity floor (`low` by default).
7. **Render:** printed the findings. The GitHub Action posts them to the pull
   request instead.

## Next Steps

- To configure severity thresholds, path filters, and token caps, see
  [Configure .lgtmaybe.yml](../how-to/configure-lgtmaybe-yml.md).
- To use a cloud provider with no API keys, see
  [Review with Bedrock OIDC](../how-to/review-with-bedrock-oidc.md) or
  [Review with Vertex WIF](../how-to/review-with-vertex-wif.md).
- To use lgtmaybe in a GitHub Actions workflow, see
  [Use as a GitHub Action](../how-to/use-as-github-action.md).
