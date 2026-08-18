## Context

See `proposal.md` for motivation. Model advice in the current README is generic, while the separate benchmark repository owns generated results for two non-comparable suites: breadth across languages and review lenses, and long-horizon behavior as Python diffs grow. The docs must remain useful when that repository gains new runs.

## Goals / Non-Goals

**Goals:**

- Give a new user separate cloud and local decision paths, with choices keyed to their priority.
- Explain the difference between benchmark suites and qualify every reported result by date and lgtmaybe version.
- Keep detailed rankings, raw data, and methodology in the benchmark repository.

**Non-Goals:**

- Reproduce the complete leaderboard in lgtmaybe's docs.
- Claim cost, latency, or provider availability that the benchmark does not establish.
- Compare scores across suites or lgtmaybe versions.
- Automate cross-repository documentation updates in this change.

## Decisions

1. Add one `docs/how-to/choose-a-review-model.md` guide organized by user decision rather than by model. A how-to page fits the task users are performing; adding the same detail to each provider guide would duplicate evidence and drift.
2. Ask users to choose cloud or local first. Within cloud, present `qwen/qwen3.8-max` for balanced, lower-noise review, `z-ai/glm-5.2` for maximum breadth recall, and `google/gemini-3.7-flash` as the fully adjudicated baseline. Within local, present `nvidia/Qwen3.6-35B-A3B-NVFP4` as the best-supported current-version breadth result without claiming it is a universal local winner.
3. Put only the cloud-versus-local decision and compact evidence in the README. Link to the guide and benchmark repository for details instead of copying a leaderboard that will age quickly.
4. Label provisional benchmark results and state the comparison key. Percentages will be copied from generated benchmark output at commit `27392b1` and described as a 2026-08-19 snapshot.
5. Add the guide to MkDocs navigation and the README's documentation index so both rendered-site and repository readers can find it.

## Risks / Trade-offs

- [Model rankings change as new runs land] → Date the guidance, link the live leaderboard, and avoid claiming permanence.
- [Readers treat different suite scores as directly comparable] → Explain that breadth and long-horizon answer different questions and keep their metrics separate.
- [Provisional results are read as audited ground truth] → Mark them provisional and explain that adjudication is high but audit traces were unavailable for those runs.
- [Sparse comparable local results invite a false local leaderboard] → Name the best-supported current-version local breadth run, disclose the limited comparison set, and direct readers to test on their hardware.
