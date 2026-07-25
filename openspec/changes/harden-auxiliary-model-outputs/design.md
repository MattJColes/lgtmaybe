## Context

The diagram pass currently asks the model for raw Mermaid and ASCII strings. `_mermaid_ok` accepts any response beginning with a flowchart keyword, so malformed statements can reach GitHub. `/ask` has the opposite problem: it requests prose without a response schema and posts the provider text unchanged, allowing an unrelated JSON envelope to appear as the answer.

## Goals / Non-Goals

**Goals:**

- Make Mermaid syntax deterministic and owned by lgtmaybe.
- Preserve useful diagram content through a typed graph schema.
- Ensure `/ask` posts either a validated answer or a clear safe fallback.
- Keep providers that ignore `response_format` usable through the existing lenient JSON parser.

**Non-Goals:**

- Building a general Mermaid parser or adding a Mermaid runtime dependency.
- Adding a new live-model eval framework; the existing eval harness scores review findings, not auxiliary presentation output.
- Changing slash-command syntax, Action inputs, or provider selection.

## Decisions

1. **Return graph primitives, not Mermaid.** `DiagramResult` will contain typed nodes and edges. Node ids are references only; the renderer assigns stable Mermaid ids, escapes all visible text, limits the graph to six unique nodes, and drops edges whose endpoints are absent. This makes the emitted Mermaid a small grammar controlled entirely by lgtmaybe.

2. **Render both views from the same graph.** Mermaid and ASCII output will be derived from the validated nodes and edges, preventing the two views from disagreeing. The existing optional ASCII field remains only as a compatibility fallback when a weak model returns legacy C4-plus-ASCII data.

3. **Use a task-specific answer envelope.** `/ask` will request `AnswerResult` through `response_format` when structured output is enabled and parse it leniently when a provider drops schema support. A wrong-schema JSON response receives a fixed retry message rather than being posted verbatim; raw non-JSON prose remains a compatibility fallback.

4. **Use deterministic tests instead of live evals in this change.** Regression tests will replay the exact problematic label and review-shaped JSON. A future auxiliary-output eval can measure model schema adherence, but runtime safety must not depend on an eval score.

## Risks / Trade-offs

- **[Graph edges may reference missing or duplicate ids]** → Keep the first unique node and omit invalid edges while still rendering the remaining graph.
- **[Some weak models may continue returning legacy raw diagram fields]** → Render only their ASCII fallback; never place model-authored Mermaid in a Mermaid fence.
- **[A provider may ignore the `/ask` schema and return prose]** → Preserve non-JSON prose, but reject object/array-shaped output that does not validate as an answer.
