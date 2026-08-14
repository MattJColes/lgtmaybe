## Context

The profiler records provider timing and token usage before the engine parses a response. A successful row therefore cannot show whether the response contained zero findings or whether findings disappeared later in the pipeline.

## Goals / Non-Goals

**Goals:**

- Make the existing opt-in profile locate finding loss at the call boundary or downstream pipeline.
- Preserve default output, provider contracts, and clean-review semantics.

**Non-Goals:**

- Judge whether a genuinely empty model answer is correct.
- Persist prompts, raw model responses, or source code.
- Add provider-specific retries or routing rules.

## Decisions

- Add an optional parsed-findings count to the existing call record. `None` keeps non-review calls and provider failures distinct from a valid zero.
- Record successful review calls only after parsing, so the same row can carry either a count or the parse error. This reuses the profiler instead of adding an audit subsystem.
- Record the final returned count once at the end of the engine pipeline. The profile compares the sum parsed from review calls with that final count; a difference localises loss to dedupe, suppression, reflection, or filtering.
- Keep raw response text out of diagnostics. Counts and parse errors answer the issue without persisting model-authored source text.

## Risks / Trade-offs

- [Summed parsed findings include duplicates across lenses] → Label the number as parsed, not unique; the final count intentionally shows downstream reduction.
- [Non-review calls have no finding count] → Render a dash so reflection and triage are never mistaken for empty review lenses.
- [Profile table format changes] → Keep the existing columns and append one fixed-width field; update the benchmark parser separately if it needs structured ingestion.
