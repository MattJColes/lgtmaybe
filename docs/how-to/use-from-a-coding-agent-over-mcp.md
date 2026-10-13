---
description: Run lgtmaybe as an MCP server so Claude Code, Cursor and other coding agents can review your local changes as a tool call.
---

# Use from a Coding Agent over MCP

`lgtmaybe mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io/)
server on stdio. A coding agent such as Claude Code or Cursor connects to it and
reviews your local changes by calling a tool, instead of running
`lgtmaybe review` in a shell and parsing what it prints. The review is the same
one `lgtmaybe review` runs: the same engine, the same config files, the same
credentials. Nothing is posted anywhere.

## Contents

- [Install the MCP Extra](#install-the-mcp-extra)
- [Add It to Claude Code](#add-it-to-claude-code)
- [Add It to Another MCP Client](#add-it-to-another-mcp-client)
- [The Tools](#the-tools)
- [Credentials and Config](#credentials-and-config)
- [Troubleshooting](#troubleshooting)
- [See Also](#see-also)

## Install the MCP Extra

The MCP SDK is an optional extra, so a plain install stays small. Install it
with pip (or `uv tool install` / `pipx install`, which put `lgtmaybe` on your
`PATH` in an isolated environment):

```bash
pip install 'lgtmaybe[mcp]'
```

The Homebrew formula and the Windows executable do not include the extra; use
pip for `lgtmaybe mcp`. Without it the command exits with a message naming the
extra to install.

## Add It to Claude Code

From the repository you want reviewed, register the server with `claude mcp add`.
Everything after `--` is the command Claude Code runs; the `lgtmaybe mcp` flags
apply to every review the agent asks for:

```bash
claude mcp add lgtmaybe -- lgtmaybe mcp \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434
```

Claude Code starts the server in the project directory, so the review sees that
repository. Add `--scope project` to write the entry to the repository's
`.mcp.json` and share it with your team (it holds the command and flags only,
never a key).

Then ask the agent for a review in plain words, for example:

> Review my uncommitted changes with lgtmaybe and fix anything high severity.

## Add It to Another MCP Client

Most clients (Cursor, Windsurf, Claude Desktop and others) read an
`mcpServers` block from a JSON config file. Add an entry that runs
`lgtmaybe mcp`. Pass `-C` with the repository path when the client does not
start servers inside your project:

```json
{
  "mcpServers": {
    "lgtmaybe": {
      "command": "lgtmaybe",
      "args": [
        "mcp",
        "--provider", "openai",
        "--model", "gpt-5.5",
        "-C", "/path/to/your/repo"
      ]
    }
  }
}
```

If the client cannot find `lgtmaybe`, give the full path to the executable
(`which lgtmaybe` prints it) as `command`.

## The Tools

The server offers two tools. Both take the same optional arguments and run one
full review per call.

| Tool | Returns |
| --- | --- |
| `review_changes` | The findings as JSON, in the same shape as `lgtmaybe review --format json`, plus the `summary` line and `incomplete` (true when some review calls did not finish, so the findings may be partial). |
| `review_instructions` | The findings as plain-text correction instructions, the same text as `lgtmaybe review --format agent`. |

| Argument | Meaning |
| --- | --- |
| `mode` | `branch` (default): committed branch changes against the base. `working`: branch commits plus uncommitted edits. `uncommitted`: only the uncommitted edits against `HEAD`. |
| `base` | The base ref to diff against, like `--base`. Defaults to the remote's primary branch. |
| `min_severity` | Drop findings below `info`, `low`, `medium`, `high` or `critical`. |
| `preset` | `fast` (four model calls) or `full` (one call per review category). |

A review that cannot run (not a Git repository, a missing credential, a provider
error) comes back as a tool error with the reason, so the agent sees what went
wrong rather than an empty result. The server keeps running for the next call.

Reviews run one at a time. A review makes several model calls, so it takes as
long as `lgtmaybe review` does; ask for one per change set rather than after
every edit.

## Credentials and Config

Config loads exactly as it does for `lgtmaybe review`: your user config
(`lgtmaybe config`), then the repository's `.lgtmaybe.yml`, then the flags on
`lgtmaybe mcp`, then the tool call's own arguments. The config files are read on
every call, so an edit to `.lgtmaybe.yml` applies without restarting the server.
A config file that does not parse stops the server at startup, where the client
shows the error.

API keys come from the environment, as they do for the CLI (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY` and so on; see the provider how-tos). The server inherits
the environment the client starts it with, so export the key in the shell you
start the client from. If your client starts servers without your shell
environment, set the key in the entry's `env` block instead, keeping in mind
that this stores it in the client's config file. lgtmaybe itself never writes a
key to disk. Keyless providers need no key at all: ollama, and Bedrock, Vertex
or Azure with ambient cloud credentials.

## Troubleshooting

- **The client says the server failed to start.** Run the same command in a
  terminal. A missing extra or a broken config prints its reason and exits.
  Otherwise the command waits silently for a client, which means it works.
- **The tool error says it is not a Git repository.** The server started outside
  your project. Add `-C /path/to/your/repo` to its arguments.
- **Where are the logs?** On stderr, which the client usually records in its MCP
  log. stdout carries only the protocol, so nothing lgtmaybe logs can corrupt it.

## See Also

- [Fix findings with an AI agent](fix-findings-with-an-ai-agent.md) - the same
  review-and-fix loop through files, without MCP
- [Configure .lgtmaybe.yml](configure-lgtmaybe-yml.md) - every option a review reads
- [Install the CLI](install-the-cli.md) - pip, Homebrew and WinGet installs
