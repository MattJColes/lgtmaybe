## Context

`ReviewFinding` reaches the GitHub adapter carrying two engine-derived signals
the adapter ignores:

- `category` — the id of the lens whose call produced the finding, overwritten
  by `engine._stamp_categories` so the model cannot claim it. Already drives the
  `possible-security-issue` label and category-matched `finding_rules`.
- `confidence` — the reflection auditor's 0-10 score for "is this real", never
  self-reported by the reviewing model. Already gates `min_confidence` and
  already prints in `lgtmaybe review` output.

The GitHub title line is `**[SEVERITY] Title**` and has been since the adapter
was written. The hidden ids that make re-runs idempotent live *below* the visible
prose: `finding_fingerprint(path, title)` and `finding_identity(finding)` are
both computed from the finding's fields, and `_finding_keys` recovers them by
regexing the marker comments out of a posted body. Nothing in that machinery
reads the title line.

## Goals / Non-Goals

**Goals:**

- Show the originating lens and the auditor's confidence on every posted
  finding, on all three surfaces, so the three read the same.
- Change nothing about dedupe, resolve-on-fix, or idempotent updates — including
  for comments posted before this change.
- Render nothing empty when a value is absent.

**Non-Goals:**

- A config knob to turn the badge off (see the proposal — `reflect: false` is
  already the opt-out for the noisy half).
- A PR-level confidence score in the summary line.
- Changing the frozen `confidence` scale to Greptile's `n/5`.
- Backfilling badges onto comments already posted (see the asymmetry below).

## Decisions

**Inside the severity brackets, separated by `·`.** The title line already opens
with a bracketed metadata group, so `**[HIGH · security · 8/10] Title**` extends
a shape the reader has already parsed rather than adding a second one. The
alternatives were a trailing suffix (`**[HIGH] Title** · security · 8/10`),
which competes with the title for the eye and wraps awkwardly on a narrow diff
view, and Markdown badge images, which are an external fetch a CSP-ish corporate
proxy may not serve and which look like ornament. A middle dot rather than a
pipe or slash: it separates without reading as a delimiter, and it survives
GitHub's Markdown untouched.

**Ordering: severity, lens, confidence.** Severity is what a reader triages on
and stays leftmost, unmoved. The lens explains *why this was looked for*, the
score *how much to trust it* — so provenance precedes credence, and the two
optional fields fall off the right end as they become absent, keeping the
line's left edge stable.

**A badge requires a category.** Confidence without a category cannot occur in
the pipeline (every lens stamps one), and a lone `· 8/10` would read as a score
attached to nothing. Requiring the category keeps the helper's output to three
shapes and makes "no badge at all" exactly the legacy rendering.

**`0` renders.** `confidence` is `int | None`; the check is `is None`, not
truthiness. A 0/10 verdict on a finding that survived `min_confidence: 0` is a
real and useful warning, and the falsy-vs-None trap is the obvious bug here, so
it has its own test.

**The category is defanged.** It is trusted config (a built-in `ReviewCategory`
value, a `scan:<tool>` id, or a `CustomLens.id` from `.lgtmaybe.yml`), not model
prose — but it now reaches rendered Markdown, and running it through the same
`_defang_fences` as the title costs one call and removes the question.

## Risks / Trade-offs

**A badged title line could break dedupe.** It cannot: both ids are computed
from fields, and `_finding_keys` reads only the marker comments. The risk worth
guarding is the *upgrade* case — a comment posted by an older version has an
unbadged title, and if matching read the visible prose the first post-upgrade
run would re-post every finding on every open PR. A test pins that a pre-badge
body still suppresses its re-post.

**Confidence on an inline comment is frozen at first post.** The review-update
endpoint cannot edit inline comments, so a comment posted at 8/10 keeps that
badge even if a later run scores the same finding differently. Demoted and broad
findings live in the review body, which *is* rewritten in place, so their badges
do track. This asymmetry is pre-existing — it applies to the title and severity
today — so it is documented rather than fixed here.

## Migration Plan

None. The change is additive to rendered prose; findings without a category are
unchanged, and existing comments keep working.

## Open Questions

None.
