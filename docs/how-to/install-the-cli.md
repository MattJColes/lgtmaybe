---
description: Install the lgtmaybe CLI with pip (any OS), Homebrew (macOS/Linuxbrew), or winget (Windows), and add cloud extras for keyless Bedrock/Vertex/Azure.
---

# Install the CLI

Install the `lgtmaybe` command-line tool once; then point it at any provider.
This page is only about **getting the CLI onto your machine**. Choosing a model
backend — a hosted API, keyless cloud, or a local model — comes after, in the
[provider how-tos](../index.md#providers).

## Install (pip)

Works on any OS with Python 3.11+:

```bash
pip install lgtmaybe
```

Verify it:

```bash
lgtmaybe help
```

That prints the command list with usage examples; `lgtmaybe help <command>`
(e.g. `lgtmaybe help review`) shows the full option reference for one command.

Upgrade later with `pip install --upgrade lgtmaybe`.

## Install on Windows (WinGet)

The WinGet package is a portable executable for Windows x86_64 (64-bit).
Install it by its exact package identifier:

```powershell
winget install --id MattJColes.lgtmaybe --exact
```

Verify the command alias:

```powershell
lgtmaybe --help
```

Upgrade or uninstall it later with the same package identifier:

```powershell
winget upgrade --id MattJColes.lgtmaybe --exact
winget uninstall --id MattJColes.lgtmaybe --exact
```

If WinGet cannot find a newly published version, refresh its package sources
and retry:

```powershell
winget source update
```

The executable bundles ast-grep for cross-file symbol resolution, but it does
not bundle the optional cloud SDKs used by keyless Bedrock, Vertex, or Azure
authentication. Use the pip install with the matching extra for those providers.
The regular `pip install lgtmaybe` path also works on Windows.

## Install on macOS (Homebrew)

On macOS (and Linuxbrew) you can install from the project's
[Homebrew tap](https://github.com/MattJColes/homebrew-tap) instead of `pip`:

```bash
brew tap MattJColes/tap
brew trust MattJColes/tap
brew install lgtmaybe
```

The middle step is required: current Homebrew refuses to load a formula from a
third-party tap until you explicitly **trust** it (`Error: Refusing to load
formula … from untrusted tap`). This is a one-time, per-machine decision that
applies to every tap outside Homebrew's core — the tap author can't waive it for
you, by design. To trust only this one formula instead of the whole tap, use
`brew trust --formula MattJColes/tap/lgtmaybe`.

Tapping and installing in one line (`brew install MattJColes/tap/lgtmaybe`)
works too, but still needs the `brew trust …` step first — otherwise the install
stops at the untrusted-tap error.

Upgrade later with `brew upgrade lgtmaybe`.

If you tapped the earlier `MattJColes/lgtmaybe` name, switch over once with
`brew untap MattJColes/lgtmaybe && brew tap MattJColes/tap` (then `brew trust
MattJColes/tap`). Upgrades keep working on the old tap either way — GitHub
redirects the renamed repo — so this is tidying, not a break.

Homebrew installs lgtmaybe into its own isolated virtualenv, so it never touches
your system or project Python. The formula creates the venv and installs
lgtmaybe and its dependencies from prebuilt **PyPI wheels** (no compiling), so a
first install takes about a minute, mostly download time. It works on any
architecture and macOS version — there's no prebuilt bottle to match. It also
pulls the `ast-grep` formula, which lgtmaybe uses for cross-file symbol
resolution during review.

## Cloud providers need extras

The base pip install, Homebrew formula, and Windows executable cover every
**API-key** and **local** provider:

| Providers | Covered by base install? |
|---|---|
| `openai`, `anthropic`, `openrouter`, `zai` — set the provider's API key in your environment | ✅ Yes |
| `ollama`, `openai-compatible` — fully local, point `--api-base` at the server | ✅ Yes |
| `bedrock`, `vertex`, `azure` — keyless cloud (OIDC/WIF) | ❌ Needs an extra |

The **keyless cloud** providers need extra cloud SDKs that the Homebrew formula
and Windows executable do not bundle. Install the CLI from PyPI with the matching
extra:

```bash
pip install 'lgtmaybe[bedrock]'   # or [vertex] / [azure]
```

or run them through the [GitHub Action](use-as-github-action.md), where the image
bundles all three and wires up the OIDC/WIF auth for you.

## Next steps

- Run your first local review: [Getting started](../tutorial/getting-started.md).
- Post reviews on real pull requests: [Use as a GitHub Action](use-as-github-action.md).
