## Context

See `proposal.md` for the race. Diagram comments already use an idempotent marker-based upsert, but the event gate excludes `synchronize`; description comments intentionally remain open/reopen-only.

## Goals / Non-Goals

**Goals:**

- Let the newest surviving pull-request run create or refresh the automatic diagram.
- Add a concise PR change summary without adding another provider call or comment.
- Preserve the independent `auto_diagram` opt-out and `auto_describe` behavior.

**Non-Goals:**

- Detect whether an earlier run reached diagram posting.
- Change workflow cancellation, posting order, slash commands, or Mermaid/text diagram rendering.

## Decisions

Give `should_auto_diagram` its own event set containing `opened`, `reopened`, and `synchronize`, while leaving the shared open-event gate in place for auto-description. Re-running diagram generation on every synchronization is the smallest reliable fix: the existing comment upsert prevents duplicates and refreshes stale diagrams when the PR changes.

A state-aware "only if missing" check was rejected because event eligibility is decided before adapter construction, it would add a new GitHub read and gateway surface, and it would preserve a stale diagram after later pushes.

Add an optional `summary` string to `DiagramResult`, request one to three concise sentences in the diagram prompt, and render non-empty summary text immediately below the title and above the Mermaid output. Shape that prompt with the referenced MIT-licensed `i-have-adhd` skill's useful message principles: lead with the highest-impact result, keep one change per sentence, and remove preamble, process recap, filler, and tangents. Keeping the field in the existing response avoids the latency and cost of a description call; making it default to an empty string preserves compatibility with providers or fixtures that omit it.

## Risks / Trade-offs

- [Each synchronization adds one diagram model call] → The call keeps the diagram current; users who prefer lower spend retain `auto_diagram: false` and `/diagram` on demand.
- [A diagram call can fail] → Existing best-effort handling logs the failure and lets the completed review stand.
