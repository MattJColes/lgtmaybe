---
description: Turn lgtmaybe findings into instructions an AI coding agent can apply, for a local review-and-fix loop before you push.
---

# Fix Findings with an AI Agent

`lgtmaybe review` runs locally and prints [findings](../reference/glossary.md#finding);
it never posts anywhere. The `--format agent` output turns those findings into
plain correction instructions. An AI coding agent (such as Claude Code) can read
and apply them. You get a review-and-fix loop on your own machine before you
ever open a pull request.

It works with any [provider](../reference/glossary.md#provider). ollama keeps
it local and free; a cloud provider gives you a stronger reviewer for a small
cost.

## Contents

- [Print the Findings as Agent Instructions](#print-the-findings-as-agent-instructions)
- [Hand It to the Agent](#hand-it-to-the-agent)
- [Close the Loop](#close-the-loop)
- [See Also](#see-also)

## Print the Findings as Agent Instructions

Run this from inside the repo, on the branch you want reviewed. It reviews the
branch's commits against the remote primary branch. Add `--working` to include
uncommitted edits too.

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434 \
  --format agent
```

The output is a set of instructions rather than a bare listing:

```text
Code review findings for your local changes. Act as the developer and apply each
correction below: open the file at the given path and line, fix the issue, and
apply the suggested change where one is given.

[1] src/app.py:42  (HIGH)  possible NPE
    Issue: `user` may be None here.
    Suggested fix:
        if user is not None:
            do_thing(user)

1 finding(s) to address. After applying the fixes, re-run `lgtmaybe review` to
confirm they are resolved.
```

## Hand It to the Agent

Save the instructions to a file and point your agent at them:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b \
  --api-base http://localhost:11434 --format agent > review.txt
```

Only the instructions go to stdout. The token-usage footer goes to stderr, so it
stays out of the file.

Then ask the agent to work through `review.txt`. For example, in Claude Code:

> Apply the corrections in review.txt, then delete it.

Each finding carries a path, a line, the issue and often a suggested
replacement. The agent can make the edit without guessing.

## Close the Loop

Once the agent has applied the fixes, run the review again to confirm the
findings are gone. By default `lgtmaybe review` reviews only your committed
branch changes. Either commit the fixes first, or add `--working` to include
uncommitted edits:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b \
  --api-base http://localhost:11434 --format agent --working
```

A clean branch prints `No review findings: nothing to correct.` followed by
the summary line. Repeat until you are happy, then open your PR. To post reviews
on the PR itself, set up the [GitHub Action](use-as-github-action.md).

## See Also

- [Use from a coding agent over MCP](use-from-a-coding-agent-over-mcp.md) - let
  the agent call the review as a tool, with no files in between
- [Run locally with ollama](run-locally-with-ollama.md) - the local CLI setup
- [What gets reviewed](../explanation/what-gets-reviewed.md) - scope, caps, and output formats
