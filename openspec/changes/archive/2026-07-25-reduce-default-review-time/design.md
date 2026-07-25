## Context

GitHub Actions run `lgtmaybe` with a 20-minute job timeout. The review engine
already has a 600-second soft deadline, but it deliberately waits for in-flight
calls. A real OpenRouter request exceeded the configured 60-second provider
timeout and held the four-call executor open until GitHub cancelled the job.

Recent run profiles also show that the combined tests/documentation call can be
the slowest default lens: one 18-file run spent 129 seconds and 7,619 output
tokens on it without producing a finding.

## Goals / Non-Goals

**Goals:**

- Prevent one stuck provider request from consuming the whole GitHub job.
- Reduce everyday model calls without weakening security or correctness.
- Preserve full and explicitly selected review coverage.
- Preserve automatic C4 diagrams in supplied workflows.
- Make future dogfood latency easy to diagnose.

**Non-Goals:**

- Remove self-reflection or its false-positive protection.
- Change local-provider timeout defaults.
- Add a new preset or dependency.
- Generate diagrams on every synchronize event.

## Decisions

- `fast` runs security, correctness/intent, and combined code-health calls.
  Tests and documentation remain available through `full` or `categories`.
- Wrap the synchronous LiteLLM request in a daemon thread and wait on its result
  for the request timeout. This is the smallest adapter-level guard that works
  even when an HTTP client's own timeout is not honored. Python cannot safely
  stop the blocked transport thread, so it is allowed to finish as a daemon.
- Keep the review deadline soft. The request guard makes in-flight duration
  bounded without changing the existing partial-results semantics.
- Set `profile: true` only in the dogfood workflow. Per-call structured timing
  logs already exist for users; the readable summary is useful for maintainers
  and costs no model calls.

## Risks / Trade-offs

- Default reviews no longer flag test or documentation gaps. Those are lower
  risk than security/correctness defects and remain one setting away.
- A timed-out request can continue consuming provider resources briefly in a
  daemon thread. The alternative is a child process per request, which adds
  substantial state and portability complexity.
- Reducing calls may reduce recall on softer concerns. The deep `full` preset
  and explicit categories remain unchanged.
