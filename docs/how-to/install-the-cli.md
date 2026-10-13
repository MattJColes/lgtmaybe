---
description: Install the lgtmaybe CLI with pip (any OS), Homebrew (macOS/Linuxbrew), or winget (Windows), and add cloud extras for keyless Bedrock/Vertex/Azure.
---

# Install the CLI

Install the `lgtmaybe` command-line tool once, then point it at any provider.
This page covers getting the CLI onto your machine. Choosing a model backend (a
hosted API, keyless cloud, or a local model) comes after: see the
[provider how-tos](../index.md#providers) and
[Choose a review model](choose-a-review-model.md).

## Install (pip)

Works on any OS with Python 3.11+:

```bash
pip install lgtmaybe
```

Verify it:

```bash
lgtmaybe --help
```

That prints the command list with usage examples. `<command> --help` (for
example `lgtmaybe review --help`) lists every option for one command.

To check which build you are running, for a bug report or a benchmark record:

```bash
lgtmaybe --version
```

It prints one line, `lgtmaybe <version>`, for every install method (pip,
Homebrew, or the Windows executable). A source checkout that was never
installed has no version metadata and prints `lgtmaybe unknown`.

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

The executable bundles ast-grep for cross-file symbol resolution. It does not
bundle the optional cloud SDKs for keyless Bedrock, Vertex, or Azure, so use
pip with the matching extra for those providers (see
[below](#cloud-providers-need-extras)). `pip install lgtmaybe` also works on
Windows.

## Install on macOS (Homebrew)

On macOS (and Linuxbrew) you can install from the project's
[Homebrew tap](https://github.com/MattJColes/homebrew-tap) instead of `pip`:

```bash
brew tap MattJColes/tap
brew trust MattJColes/tap
brew install lgtmaybe
```

The middle step is required. Current Homebrew refuses to load a formula from a
third-party tap until you explicitly **trust** it (`Error: Refusing to load
formula … from untrusted tap`). You decide this once per machine, for every tap
outside Homebrew's core, and a tap author cannot waive it for you. To trust
only this formula instead of the whole tap, use
`brew trust --formula MattJColes/tap/lgtmaybe`.

`brew install MattJColes/tap/lgtmaybe` taps and installs in one line, but still
needs the `brew trust` step first or it stops at the untrusted-tap error.

Upgrade later with `brew upgrade lgtmaybe`.

If you tapped the earlier `MattJColes/lgtmaybe` name, switch over once with
`brew untap MattJColes/lgtmaybe && brew tap MattJColes/tap`, then
`brew trust MattJColes/tap`. Upgrades keep working on the old tap because GitHub
redirects the renamed repo, so switching is optional tidying.

Homebrew installs lgtmaybe into its own virtualenv, so it never touches your
system or project Python. The formula installs lgtmaybe and its dependencies
from prebuilt **PyPI wheels** (no compiling), so a first install takes about a
minute, mostly download time. There is no prebuilt bottle, so it works on any
architecture and macOS version. It also installs the `ast-grep` formula, which
lgtmaybe uses for cross-file symbol resolution.

## Cloud Providers Need Extras

The base pip install, Homebrew formula, and Windows executable cover every
API-key and local provider:

| Providers | Covered by base install? |
|---|---|
| `openai`, `anthropic`, `openrouter`, `zai`: set the provider's API key in your environment | ✅ Yes |
| `ollama`, `openai-compatible`: point `--api-base` at the server (local, or a hosted OpenAI-compatible API) | ✅ Yes |
| `bedrock`, `vertex`, `azure`: keyless cloud (OIDC/WIF) | ❌ Needs an extra |

The keyless cloud providers need cloud SDKs that the Homebrew formula and
Windows executable do not bundle. Install the CLI from PyPI with the matching
extra:

```bash
pip install 'lgtmaybe[bedrock]'   # or [vertex] / [azure]
```

Or run them through the [GitHub Action](use-as-github-action.md), whose image
bundles all three SDKs and handles the OIDC/WIF exchange for you.

To use `--static-analysis` locally, install the tools it drives with
`pip install 'lgtmaybe[static-analysis]'` (ruff, bandit, mypy, semgrep, and
zizmor). gitleaks and osv-scanner are separate binaries you install yourself;
any tool that is not installed is skipped. The Action image bundles all of
them.

To serve reviews to a coding agent with `lgtmaybe mcp`, install the MCP SDK
with `pip install 'lgtmaybe[mcp]'`. The Homebrew formula and Windows executable
do not include it. See
[Use from a coding agent over MCP](use-from-a-coding-agent-over-mcp.md).

## Next Steps

- Run your first local review: [Getting started](../tutorial/getting-started.md).
- Diagram what a change touches: [Generate a change overview](generate-a-change-diagram.md).
- Post reviews on real pull requests: [Use as a GitHub Action](use-as-github-action.md).
