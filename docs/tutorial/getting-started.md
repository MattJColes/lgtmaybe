---
description: Run your first lgtmaybe review locally with ollama, with no API keys, no GitHub token, and no pull request.
---

# Getting Started with lgtmaybe

This tutorial walks you through your first review using **ollama**. ollama runs
the model on your own machine, costs nothing, and needs no API keys. By the end
you will have reviewed a branch and seen the findings in your terminal. You need
no GitHub token and no pull request.

Two words first:

- A [finding](../reference/glossary.md#finding) is one issue the review
  reports. It has a [severity](../reference/glossary.md#severity), a title and
  an explanation, and sometimes a suggested fix.
- A [lens](../reference/glossary.md#lens) is one focused review question, such
  as "is this secure?". lgtmaybe runs several lenses and merges their findings.

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

[Install the CLI](../how-to/install-the-cli.md) explains three things: the
`brew trust` step, the Windows (WinGet) install, and which providers the base
install covers. API-key and local providers work out of the box. Keyless cloud
providers need a `pip` extra.

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

## Step 3: Point lgtmaybe at ollama

lgtmaybe has no built-in model, so tell it once which provider and model to
use:

```bash
lgtmaybe config init
```

It finds the running ollama and the models you have pulled, so you only
confirm:

```console
Found ollama running at http://localhost:11434 (1 model pulled) - use ollama? [Y/n]:
Pulled models: qwen3.6:27b
Model [qwen3.6:27b]:
Checking ollama / qwen3.6:27b with one short request...
OK: the model answered.
Wrote /home/you/.config/lgtmaybe/config.yml
Next: lgtmaybe review
```

Press Enter twice. Before it writes anything, it sends the model one tiny
request. A stopped server or a model you have not pulled fails here, not
halfway through a review.

The file holds only the provider and model. If you later use a hosted provider,
its API key stays in your environment.

If ollama is on another machine, answer `n`, choose `ollama`, and enter its
address. You can also skip `config init` and pass `--provider ollama --model
qwen3.6:27b` to each command. Without either, `lgtmaybe review` stops straight
away and tells you to run `config init`.

## Step 4: Review Your Changes

From inside a git repo, on a branch with some changes, run:

```bash
lgtmaybe review
```

lgtmaybe diffs your current branch against the remote primary branch. It tries
`origin/HEAD` first, then falls back to `origin/main` / `origin/master`, then a
local `main` / `master`. It sends the diff, with some surrounding lines for
context, to your local qwen3.6:27b model. Then it prints the findings to your
terminal:

```console
src/app.py:14  [MEDIUM] Missing None check before use (confidence 8/10)
  find_user() returns None for an unknown id, so user.email raises AttributeError.

1 finding · risk low (no risk signals) · provider ollama · model qwen3.6:27b · lgtmaybe 2.11.1
```

Each finding shows its file and line, severity and title, then the explanation.
The last line does three things:

- counts the findings
- rates the [risk of the change](../reference/glossary.md#risk-of-change)
- names the model and lgtmaybe version

Three flags change what gets reviewed:

- `--working` reviews the whole worktree (your branch's commits plus
  uncommitted edits).
- `--uncommitted` reviews only the uncommitted edits.
- `--base main` diffs against a different base.

`lgtmaybe review --help` lists the everyday options. `--help-all` adds the
advanced tuning ones.

## Step 5: Change the Output Format

`--format` controls what `review` prints. `--json` (shorthand for
`--format json`) emits a JSON array ready to pipe into other tooling:

```bash
lgtmaybe review --json
```

`--format agent` prints the findings as correction instructions that an AI
coding agent can read and apply. Use it for a local review-and-fix loop. See
[Fix findings with an AI agent](../how-to/fix-findings-with-an-ai-agent.md).

`--format sarif` prints a [SARIF](../reference/glossary.md#sarif) 2.1.0 log.
You can upload it to GitHub code scanning or open it in an IDE. See
[Upload findings as SARIF](../how-to/upload-findings-as-sarif.md).

## Step 6: See the Whole Change

`lgtmaybe diagram` sends the same local diff to three concurrent model calls.
It prints the
[**change overview**](../reference/glossary.md#change-overview):

- a description of what you changed
- a risk-of-change rating (computed locally, no model call)
- a [**High Impact Areas**](../reference/glossary.md#high-impact-areas) section
  calling out anything that could bite, such as infrastructure, security
  posture, outage risk, migrations, or backups
- a diagram of the components your change touches, plus a sequence diagram
  when it alters a run-time flow

```bash
lgtmaybe diagram
```

It takes the same `--base` / `--working` / `--uncommitted` flags as `review`.

Run `review` then `diagram` before you open a pull request. The first tells you
what's wrong with the change. The second tells you what the change is and what
it could break.

The diagrams print as Mermaid source plus a text rendering for the terminal.
Paste the Mermaid into a GitHub comment or [mermaid.live](https://mermaid.live)
to see it drawn. See
[Generate a change overview](../how-to/generate-a-change-diagram.md).

## Step 7: Post Reviews on Real Pull Requests

The CLI reviews local changes. To post inline comments and a summary on real
pull requests, wire lgtmaybe into your code host. The review is the same on
every host; only the setup differs:

- **GitHub:** add the Action to your repo.
  See [Use as a GitHub Action](../how-to/use-as-github-action.md).
- **GitLab:** add a CI job.
  See [Review on GitLab](../how-to/review-on-gitlab.md).
- **Gitea:** add a Gitea Actions workflow.
  See [Review on Gitea](../how-to/review-on-gitea.md).
- **Azure DevOps:** add an Azure Pipelines build validation policy.
  See [Review on Azure DevOps](../how-to/review-on-azure-devops.md).

## What Happened Under the Hood

lgtmaybe ran its pipeline over your local diff:

1. **Fetch:** read the diff from your local repo with `git`.
2. **Filter:** skipped generated files, binaries, and lockfiles. It
   [redacted](../reference/glossary.md#redaction) anything that looked like a
   secret.
3. **Review:** sent five concurrent model calls, one per concern: security,
   correctness, code health, artefacts (tests and documentation), and
   architecture. This is the default `fast`
   [preset](../reference/glossary.md#preset). Each call asked for findings as
   JSON.
4. **Parse and anchor:** validated each reply against the findings schema. It
   pinned every finding to the changed line it quotes (its
   [anchor](../reference/glossary.md#anchor)).
5. **Reflect:** asked the model to audit its own findings
   ([reflection](../reference/glossary.md#reflection)). It dropped the ones the
   model could not stand behind.
6. **Filter findings:** dropped defect findings with no concrete failure
   scenario. It also dropped anything below the severity floor (`low` by
   default).
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
