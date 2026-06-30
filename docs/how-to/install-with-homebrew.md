---
description: Install the lgtmaybe CLI on macOS or Linuxbrew from the project's Homebrew tap instead of pip.
---

# Install with Homebrew

On macOS (and Linuxbrew), you can install the `lgtmaybe` CLI from the project's
[Homebrew tap](https://github.com/MattJColes/homebrew-lgtmaybe) instead of `pip`.

```bash
brew install MattJColes/lgtmaybe/lgtmaybe
```

That one command taps the repository and installs the formula. If you prefer to
tap first:

```bash
brew tap MattJColes/lgtmaybe
brew install lgtmaybe
```

Verify it:

```bash
lgtmaybe --help
```

Upgrade later with:

```bash
brew upgrade lgtmaybe
```

Homebrew installs lgtmaybe into its own isolated virtualenv, so it never touches
your system or project Python. The first install builds a few native
dependencies from source (pydantic-core, tiktoken, tokenizers), which can take a
minute. It also pulls the `ast-grep` formula, which lgtmaybe uses for cross-file
symbol resolution during review.

## Which providers does the Homebrew build cover?

The formula installs the core dependencies, which covers every **API-key** and
**local** provider:

- `openai`, `anthropic`, `openrouter`, `zai` — set the provider's API key in your
  environment.
- `ollama` and `openai-compatible` — fully local, point `--api-base` at the server.

The **keyless cloud** providers — `bedrock`, `vertex`, `azure` — need extra cloud
SDKs that the Homebrew formula does not bundle. For those, install the CLI from
PyPI with the matching extra, e.g.:

```bash
pip install 'lgtmaybe[bedrock]'   # or [vertex] / [azure]
```

or run them through the [GitHub Action](use-as-github-action.md), where the image
bundles all three and wires up the OIDC/WIF auth for you.

## Next steps

- Run your first local review: [Getting started](../tutorial/getting-started.md).
- Post reviews on real pull requests: [Use as a GitHub Action](use-as-github-action.md).
