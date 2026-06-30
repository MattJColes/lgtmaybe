---
description: Install the lgtmaybe CLI on macOS or Linuxbrew from the project's Homebrew tap instead of pip.
---

# Install with Homebrew

On macOS (and Linuxbrew), you can install the `lgtmaybe` CLI from the project's
[Homebrew tap](https://github.com/MattJColes/homebrew-lgtmaybe) instead of `pip`.

```bash
brew tap MattJColes/lgtmaybe
brew trust MattJColes/lgtmaybe
brew install lgtmaybe
```

The middle step is required: current Homebrew refuses to load a formula from a
third-party tap until you explicitly **trust** it (`Error: Refusing to load
formula … from untrusted tap`). This is a one-time, per-machine decision that
applies to every tap outside Homebrew's core — the tap author can't waive it for
you, by design. To trust only this one formula instead of the whole tap, use
`brew trust --formula MattJColes/lgtmaybe/lgtmaybe`.

Tapping and installing in one line (`brew install MattJColes/lgtmaybe/lgtmaybe`)
works too, but still needs the `brew trust …` step first — otherwise the install
stops at the untrusted-tap error.

Verify it:

```bash
lgtmaybe --help
```

Upgrade later with:

```bash
brew upgrade lgtmaybe
```

Homebrew installs lgtmaybe into its own isolated virtualenv, so it never touches
your system or project Python. The formula creates the venv and installs
lgtmaybe and its dependencies from prebuilt **PyPI wheels** (no compiling), so a
first install takes about a minute, mostly download time. It works on any
architecture and macOS version — there's no prebuilt bottle to match. It also
pulls the `ast-grep` formula, which lgtmaybe uses for cross-file symbol
resolution during review.

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
