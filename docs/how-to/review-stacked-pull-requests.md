---
description: How lgtmaybe reviews a stack of pull requests, where each one builds on the one below.
---

# Review Stacked Pull Requests

A stack is a chain of small pull requests, each built on the one below it. You
review and merge them from the bottom up. GitHub supports stacks natively, and
tools such as Graphite, ghstack and git-town build them from ordinary branches.

lgtmaybe reviews each pull request in a stack on its own. It also knows where
that pull request sits, so its findings make sense for one layer of a bigger
change.

## Contents

- [What lgtmaybe Does](#what-lgtmaybe-does)
- [How It Finds the Stack](#how-it-finds-the-stack)
- [Set Up the Workflow](#set-up-the-workflow)
- [Review a Stacked Branch Locally](#review-a-stacked-branch-locally)
- [Other Code Hosts](#other-code-hosts)
- [See Also](#see-also)

## What lgtmaybe Does

Each pull request in a stack shows only its own changes: its layer. lgtmaybe
reviews that layer and nothing else. On GitHub it adds three things.

**It tells the model about the stack.** Every
[lens](../reference/glossary.md#lens) learns the layer's position, such as
"layer 2 of 3", and the branch the stack lands on. It also gets the number,
title and changed files of every other open layer. Code this layer uses may
come from a layer below, and a gap it leaves may be filled by a layer above.
Knowing that, the model hedges such claims instead of reporting them as bugs.
The review summary names the layer too.

The other layers' titles and paths are handled like the diff. They are
[redacted](../reference/glossary.md#redaction) before they leave, and the model
is told they are data, not instructions. lgtmaybe names at most eight layers on
each side.

**It skips a rebase that changes nothing.** When a lower pull request merges,
GitHub rebases every pull request above it. Each one gets a new commit, but its
diff is the same. lgtmaybe stamps a fingerprint of the whole diff next to its
[watermark](../reference/glossary.md#watermark). When a new commit's diff
matches it, the run stops without calling the model or posting anything.

The fingerprint ignores line numbers and blob ids, which a rebase moves. Any
change to an added, removed or surrounding line still counts, so that layer is
reviewed again. The skip is part of
[incremental review](../reference/glossary.md#incremental-review), which is on
by default for pushes.

**It reviews a retargeted pull request.** Tools that chain branches move a pull
request onto its parent's base when the parent merges. GitHub reports that as
an `edited` event that changes the base. The head commit stays the same, but
the diff changes. lgtmaybe runs a full review and refreshes the change
overview. Any other edit, such as a new title, runs nothing.

## How It Finds the Stack

GitHub's native stacks put a `stack` object on each pull request. lgtmaybe
reads it, then lists the layers through GitHub's stacks API.

Without that object, lgtmaybe follows branch names. When the pull request's base
is not the default branch, it looks for the open pull request whose head is that
base, and repeats downwards. It then looks for open pull requests based on this
one's head, and repeats upwards. A step with more than one match ends the walk
on that side. Only branches in the same repository count, so a pull request from
a fork is never walked.

The lookup is best effort. If it fails, the review runs without stack context
and the failure is logged.

## Set Up the Workflow

The starter workflows in
[`examples/workflows/`](https://github.com/MattJColes/lgtmaybe/tree/main/examples/workflows)
already listen for the events a stack needs. If you wrote your own workflow,
add `edited` to the trigger:

```yaml
on:
  pull_request_target:
    types: [opened, synchronize, reopened, edited]
```

Then skip the edits that leave the base alone. Add the middle line below to the
`pull_request_target` arm of your job's `if:`, and keep the rest as it is:

```yaml
      (github.event_name == 'pull_request_target' &&
       (github.event.action != 'edited' || github.event.changes.base) &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
```

If your workflow sets `concurrency` at the top level, give those edits a group
of their own. Otherwise a title edit joins the review's group, and
`cancel-in-progress` cancels the running review. The starter workflows show how.

Every layer runs its own review, so a stack of five pull requests costs five
reviews. The rebase skip keeps the repeat cost down when lower layers merge.

## Review a Stacked Branch Locally

`lgtmaybe review` compares your branch with the repository's main branch. On a
stacked branch, that includes every layer below yours. Pass the parent branch
to review only your layer:

```bash
lgtmaybe review --base feature-a
```

The CLI does not find the parent branch for you.

## Other Code Hosts

Stack support is GitHub only. On GitLab, Gitea and Azure DevOps, each pull or
merge request is reviewed on its own, with no stack context.

## See Also

- [Use as a GitHub Action](use-as-github-action.md) - the workflow and its
  inputs.
- [Glossary: stacked pull requests](../reference/glossary.md#stacked-pull-requests).
