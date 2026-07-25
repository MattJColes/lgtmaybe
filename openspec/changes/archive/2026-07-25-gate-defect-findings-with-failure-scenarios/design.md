## Context

`ReviewFinding` currently carries a prose explanation and a changed-line anchor. The engine deterministically verifies the anchor, then the reflection pass tries to disprove the finding with the diff and available file context. Severity is still supplied by the review model, so it cannot safely decide whether a finding must provide causal evidence.

The built-in categories already separate defect claims from gap and maintainability observations. This allows the engine to apply an evidence gate without adding another classifier, provider call, dependency, or user setting.

## Goals / Non-Goals

**Goals:**

- Require concrete causal evidence for built-in categories that claim broken behaviour.
- Apply the requirement independently of model-selected severity.
- Reuse the existing reflection pass to reject invented or unsupported scenarios.
- Keep non-defect findings useful without forcing a fabricated runtime failure.
- Enable the behaviour for every review by default.

**Non-Goals:**

- Re-score finding severity.
- Prove failure scenarios with static execution or run PR code.
- Gate custom lenses whose semantics are unknown to the engine.
- Add a configuration toggle, another model call, or an A/B benchmark.
- Change the existing `--no-reflect` override or reflection's keep-all error fallback.

## Decisions

1. Name the field `failure_scenario`, not `failure_path`. `ReviewFinding.path` already means a repository file, so using `path` for causal evidence is needlessly ambiguous.

2. Add `failure_scenario: str | None = None` to `ReviewFinding`. The default preserves existing programmatic construction and custom-lens examples. The review prompt asks every built-in call to emit the field: defect findings provide a concise trigger, changed behaviour, and observable impact; other findings emit `null`.

3. Gate by the engine-stamped category, never severity. Security, correctness, deprecation, and performance findings require a non-blank scenario. Tests, documentation, complexity, intent, ponytail, and custom-lens findings do not. A missing or whitespace-only required scenario is dropped before reflection, so it consumes no audit tokens.

4. Reuse the reflection verdict's existing `keep` decision for semantic validation. The auditor receives `failure_scenario` with the finding and is instructed to drop a defect finding when the scenario is speculative, contradicts the diff or grounded file text, or relies on an unsupported causal step. No new verdict field or provider call is needed.

5. Keep existing reflection escape hatches intact. `--no-reflect` still skips semantic validation, while the deterministic presence gate remains active. If reflection itself fails, its existing keep-all fallback still applies to findings that passed the presence gate.

6. Keep `failure_scenario` as structured evidence rather than duplicating it in rendered prose. JSON output exposes the field automatically; GitHub, human, and agent renderers continue to use the concise title/body contract.

## Risks / Trade-offs

- [A model omits a scenario for a real defect] → The finding is dropped. This is the deliberate precision-over-recall trade-off requested for the default behaviour; focused acceptance tests pin it.
- [A model invents a plausible scenario] → The existing grounded reflection pass actively tries to disprove it and drops unsupported claims.
- [A weaker provider struggles with the extra field] → The field is nullable, examples show both forms, and the Pydantic default keeps parsing backwards-compatible for non-defect output.
- [A custom lens reports defects without a scenario] → Custom lenses remain exempt because the engine cannot infer their semantics without another public configuration surface.
- [The field adds output tokens] → Scenarios are constrained to one concise causal chain and reuse the existing call and audit.

## Migration Plan

Regenerate the model schema/reference artifacts and update all built-in prompt examples. The behaviour becomes active on upgrade with no config migration. Reverting the change restores the previous filter because no persisted data or external dependency is introduced.

## Open Questions

None.
