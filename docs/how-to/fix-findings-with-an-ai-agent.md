# Fix findings with an AI agent

`lgtmaybe review` runs locally and prints findings; it never posts anywhere. The
`--format agent` output turns those findings into plain correction instructions
an AI coding agent (such as Claude Code) can read and apply, so you get a
review-and-fix loop on your own machine before you ever open a pull request.

This works with any provider. ollama keeps it local and free; a cloud provider
gives you a stronger reviewer at a small cost.

## Print the findings as agent instructions

From inside the repo, on the branch you want reviewed:

```bash
lgtmaybe review \
  --provider ollama \
  --model qwen3.6:27b \
  --api-base http://localhost:11434 \
  --format agent
```

The output is directive rather than a bare listing:

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

## Hand it to the agent

Save the instructions and point your agent at them:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b \
  --api-base http://localhost:11434 --format agent > review.txt
```

Then ask the agent to work through `review.txt` — for example, in Claude Code:

> Apply the corrections in review.txt, then delete it.

Because each finding carries a path, a line, the issue, and (often) a suggested
replacement, the agent has everything it needs to make the edit without guessing.

## Close the loop

Once the agent has applied the fixes, run the review again to confirm the
findings are gone:

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b \
  --api-base http://localhost:11434 --format agent
```

A clean branch prints `No review findings — nothing to correct.` Repeat until
you are happy, then open your PR. To post reviews on the PR itself, wire up the
[GitHub Action](use-as-github-action.md).

## See also

- [Run a local or OpenAI-compatible model](run-a-local-model.md) — the local CLI setup
- [What gets reviewed](../explanation/what-gets-reviewed.md) — scope, caps, and output formats
