---
description: How lgtmaybe sizes a pull request's risk of change - low, medium, high or critical - from blast radius, sensitive paths, size and tests, with no model call.
---

# Risk of Change

Every review carries a **risk of change** verdict: `low`, `medium`, `high` or
`critical`, with the reasons that produced it. It answers a different question
from the findings. A finding says "this line is wrong". The verdict says "this
change can reach a long way, so read it carefully", even when every line is
right.

The verdict is deterministic. It makes no model call, so it costs nothing, it
reads the same on every re-run of the same head, and a workflow can rely on it.

## Where You See It

The review summary line names the level and its top reason:

```text
3 findings · risk high (`src/core/models.py` is imported by 84 files, +2 more) · provider … · model …
```

The change overview lists every reason in a **Risk of Change** section above
High Impact Areas. The summary carries the verdict even when the overview is
turned off or skipped, so every review has it.

The summary also carries a hidden marker, `<!-- lgtmaybe-risk:high -->`, and
with `pr_labels` on the PR gets a `risk/<level>` label. Locally, `lgtmaybe
review` prints the same summary line.

## How the Level Is Decided

Four factors each raise the level, and the verdict takes the highest:

| Factor | Raises to |
|---|---|
| A changed file imported by 5 or more files in the repository | `medium` |
| A changed file imported by 20 or more files, or matching a `risk.core_paths` glob | `high` |
| Touching infrastructure, security, data migrations, or backup and recovery paths | `high` |
| Touching any other High Impact area (dependencies, compatibility, cost, …) | `medium` |
| 500 or more changed lines | `medium` |
| 50 or more changed lines of code with no test file changed | `medium` |

Two distinct `high` reasons make the change `critical`: a widely imported
module and a Terraform file, say, or an auth change and a database migration.
Two files in one area count once. A change touching only documentation and
tests is `low`.

## Blast Radius

The importer count comes from one [ast-grep](https://ast-grep.github.io/) scan
of the checked-out repository for Python, TypeScript and JavaScript imports. It
parses files and runs nothing. On `pull_request_target` the checkout is the
trusted base branch, and nothing from the scan is sent to the model.

The count measures the code that already depends on a file. A file the PR adds
has no existing importers, and importers the PR itself changes are left out,
because they are already in the diff.

When a blast radius can't be measured, the verdict names the file instead of
guessing. This happens for a language the scan doesn't read, when there is no
checkout, or when the scan runs past its 20-second limit. A `low` verdict with
unmeasured files always says so:

```text
risk low (blast radius not assessed: `lib/billing.rb`)
```

The import count can't see modules reached by dynamic import, TypeScript path
aliases or barrel re-exports. Name those in `risk.core_paths` (see
[`risk`](../how-to/configure-lgtmaybe-yml.md#risk)).

## Gating on the Verdict

lgtmaybe never approves or blocks a PR on its risk. The marker lets you decide
what a level means for your team. For example, a step after the lgtmaybe step
can request a second reviewer when the review summary carries
`lgtmaybe-risk:critical`:

```yaml
- name: Require a second reviewer on critical changes
  env:
    GH_TOKEN: ${{ github.token }}
    PR: ${{ github.event.pull_request.number }}
  run: |
    if gh pr view "$PR" --json reviews --jq '.reviews[].body' \
        | grep -q 'lgtmaybe-risk:critical'; then
      gh pr edit "$PR" --add-reviewer my-org/platform-leads
    fi
```

If the assessment itself fails, the summary reads `risk unavailable` and no
marker is written, so a gate reads a missing marker as "unknown", never as a
level.
