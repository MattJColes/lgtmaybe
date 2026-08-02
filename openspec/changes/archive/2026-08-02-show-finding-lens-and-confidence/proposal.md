## Why

A reader of a posted lgtmaybe comment cannot tell which lens raised it or how
sure the reviewer was. Both answers already exist on the finding by the time it
is posted — `category` is stamped by `engine._stamp_categories` (the originating
lens) and `confidence` is the reflection auditor's 0-10 score — and both already
render in the local CLI (`cli/render.py`). Only the GitHub surface drops them,
so the one place most reviews are actually read is the least informative.

Competing reviewers surface exactly these two signals: Greptile puts a `3/5`
confidence on each comment, CodeRabbit a category badge. Neither costs us a new
model call or a new field; the values are computed today and thrown away at the
boundary.

## What Changes

- Add `_finding_badge(f)` in `github/rest_gateway.py`, returning the provenance
  suffix for a finding's title line: `""`, `" · security"`, or
  `" · security · 8/10"`.
- Render it inside the existing severity brackets on all three posting surfaces
  — the inline comment title, `_render_demoted`, and `_render_broad` — so the
  three agree.
- Omit each half when its value is absent, so a `--no-reflect` run or a
  deterministic static-analysis finding never renders an empty field, and an
  uncategorised (legacy) finding renders byte-for-byte as it does today.

Explicitly **not** in scope:

- **No config knob.** `reflect: false` already suppresses the confidence half,
  and `category` is one word that also keys `finding_rules` — surfacing it
  teaches users what to write rules against. A flag would cost a model field, a
  CLI option, an Action input, env plumbing, and a generated-reference
  regeneration for a nine-character suffix.
- **No PR-level confidence score.** `summary_template` is user-customisable; a
  new placeholder would silently change templates teams already wrote.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-posting`: A posted finding names the lens that raised it and the
  auditor's confidence in its title line, on every posting surface, without
  touching the hidden ids that key dedupe.

## Impact

Every inline comment, demoted finding, and broad observation gains up to nine
characters in its title line. Nothing else moves: the hidden fingerprint and
identity markers are computed from the finding's fields, not its rendering, so
re-run dedupe, resolve-on-fix marker rewriting, and idempotent updates are
unaffected — including for comments posted before this change, which continue
to suppress their re-post on the first run after upgrade.
