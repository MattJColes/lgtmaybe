## Context

See proposal.md for why. Three things already push against noise: the codebase-humility rules, the narration rule, and reflection. All three judge whether a finding is *true*. None judges whether it is *worth raising*, which is the gap behind trivial fixes. `min_confidence` exists but defaults to off.

## Goals / Non-Goals

**Goals:**

- Remove the architecture lens with no leftover category, section, example, ceiling, ladder rung or fixture.
- Add a "worth raising" bar at the two places a finding is decided: the lens prompt and reflection.

**Non-Goals:**

- Changing `min_confidence`, per-lens bounds or severity ceilings. Their effect on recall is unmeasured.
- Touching the opt-in `pack:design` lens. A team that asks for design review still gets it.
- Merging or removing other lenses. Ponytail and complexity stay until an eval says otherwise.

## Decisions

**Delete the enum member rather than disable the lens.** A switch would keep the prompt, tests and docs alive for something we do not want to run. Teams who want it can add a custom lens, and the id `architecture` is free for them to use.

**Ignore `architecture` in `categories` with a warning.** Action tags float, so a validation error would turn a release into red CI for any repository that listed its lenses. The alternative, failing loudly, is cleaner but costs users an outage for a value that now means nothing. The config loader holds the one retired name, because every config path runs through it. Delete it at the next major version.

**Put the bar in the shared rules.** It applies to every lens, including custom ones, and the shared preamble stays lens-independent and byte-identical across calls. Per-lens wording would duplicate it ten times.

**Frame the bar as an outcome, not a tone.** "Would a competent engineer change this before merging?" can be judged per finding. "Be less nitpicky" cannot. The rule keeps the literal `{"findings": []}` so the injection wrapper's restatement still matches.

**Reflection repeats the bar.** A lens under a "find X" mandate is the wrong judge of its own output, and reflection is already the auditor. It gets a fourth drop rule beside (a) to (c). A real defect passes by construction, because its failure scenario shows an observable difference. The keep-all default on an unparseable verdict is unchanged.

**Measure with a clean-change fixture.** `forbidden` findings already fail the `clean` bar, so one fixture of a correct diff with tempting nits gives a repeatable false-positive check, with no new eval machinery.

## Risks / Trade-offs

- [A stricter bar suppresses a real low-severity finding] → Run the eval gate on recall before and after with a live model. Unit tests cannot show this.
- [Coupling and dependency-direction bugs go unreported by default] → Accepted. `pack:design` and custom lenses remain, and correctness still reports concrete breakage.
- [The ponytail arbitration rung for architecture disappears] → `OPPOSED` keeps the correctness and security pairs, which are the ones that protect guards.
- [The retired-name validator lingers] → Its comment names the removal point.

## Migration Plan

Default configs need nothing. Configs listing `architecture` log a warning. Gates that require `architecture` in the hidden lenses marker must drop it. Rollback is a revert; old posted comments stay valid because their hidden ids are not parsed back into `ReviewCategory`.
