# Spec Delta

## Purpose

Gives every review a single, deterministic answer to "how far could this change
reach?" - a risk level with its reasons - so a reviewer can size their attention
before reading a line, and a merge gate can act on it without a model call.

## ADDED Requirements

### Requirement: Every review carries a deterministic risk verdict
<!-- anchor: risk.assess -->

With `risk.enabled` on (the default), every review SHALL assess the change's
risk as one of `low`, `medium`, `high` or `critical`, together with the reasons
that produced it. The assessment MUST NOT call the model: it derives only from
the changed paths, the diff, and a read-only scan of the workspace, so the same
PR and workspace always yield the same verdict. A failure inside the assessment
SHALL degrade to a verdict that says the assessment is unavailable and MUST NOT
fail the review. With `risk.enabled` off, no verdict, marker, section or label
is produced.

#### Scenario: default configuration
- **WHEN** a review runs with no risk configuration
- **THEN** the review summary carries a risk level and at least one reason

#### Scenario: assessment disabled
- **WHEN** `risk.enabled` is false
- **THEN** the summary, overview and labels carry no risk verdict

#### Scenario: assessment raises
- **WHEN** the workspace scan fails unexpectedly
- **THEN** the review still posts, the summary line reads `risk unavailable`, and no risk marker is carried

### Requirement: Blast radius counts the importers of each changed file
<!-- anchor: risk.blast-radius -->

For each changed file in a supported language (Python, TypeScript, JavaScript),
the assessment SHALL count the distinct workspace files that import it,
excluding the file itself. Five or more importers SHALL raise the verdict to at
least `medium`, and twenty or more to at least `high`. A changed file matching
any `risk.core_paths` glob SHALL raise the verdict to at least `high` whatever
its importer count. Each file that raises the level SHALL be named with its
count. Paths are rendered as inline code with backticks stripped.

#### Scenario: a widely imported module changes
- **WHEN** a changed Python module is imported by 84 files in the workspace
- **THEN** the verdict is at least `high` and names that module as imported by 84 files

#### Scenario: a configured core path changes
- **WHEN** `risk.core_paths` is `["src/shared/**"]` and `src/shared/clock.py` changes with two importers
- **THEN** the verdict is at least `high` and names `src/shared/clock.py` as a core path

#### Scenario: a new file
- **WHEN** a changed file does not yet exist in the workspace
- **THEN** it contributes no importers and is not reported as unassessed

### Requirement: Unmeasured blast radius is named, never scored low
<!-- anchor: risk.unassessed -->

When blast radius cannot be measured for a changed code file - no workspace
available, the scan exceeded its time limit, or the file's language is not
supported - the verdict SHALL name those files as "blast radius not assessed"
and SHALL still apply every other factor. Wherever the level renders (summary
line, overview, marker consumers aside), a `low` level MUST carry the
unassessed caveat alongside it, so an unmeasured blast radius never reads as a
measured clean result.

#### Scenario: no workspace checkout
- **WHEN** the review runs with an empty workspace and changes `src/app.py`
- **THEN** the verdict lists `src/app.py` under blast radius not assessed

#### Scenario: unsupported language
- **WHEN** only `lib/billing.rb` changes
- **THEN** the verdict names `lib/billing.rb` as not assessed and does not claim the change is low risk without that caveat

### Requirement: Factors combine into a level by fixed rules
<!-- anchor: risk.level -->

The level SHALL be the highest level any factor raises it to, and SHALL be
`critical` when two or more distinct factors each raise it to `high`. Beyond
blast radius: a High Impact path signal for infrastructure, security, data
migration, or backup and recovery SHALL raise it to `high`, and any other High
Impact path signal to `medium`; 500 or more changed lines SHALL raise it to
`medium`; and 50 or more changed lines of non-test code with no test file
changed SHALL raise it to `medium`. A change touching only documentation and
test files SHALL be `low` with that stated as its reason. Distinct means a
different factor or a different High Impact area; two files in one area count
once.

#### Scenario: core module and infrastructure together
- **WHEN** a module with 30 importers and a Terraform file both change
- **THEN** the verdict is `critical`

#### Scenario: two Terraform files
- **WHEN** only two Terraform files change
- **THEN** the verdict is `high`, not `critical`

#### Scenario: docs and tests only
- **WHEN** only `README.md` and `tests/test_api.py` change
- **THEN** the verdict is `low` with the reason "docs and tests only"

### Requirement: The verdict covers the whole pull request
<!-- anchor: risk.whole-pr -->

The verdict SHALL be assessed over every file and line the pull request changes,
before any incremental, triage or file-cap scoping narrows what the lenses
review, so a re-review after a small push reports the same level as a full
review of the same head.

#### Scenario: incremental re-review
- **WHEN** a PR whose full change is `high` gets a one-line follow-up commit reviewed incrementally
- **THEN** the summary still reports `high` with the same reasons

### Requirement: The summary carries the verdict and a hidden marker
<!-- anchor: risk.summary -->

Every review summary SHALL name the level and its highest-ranked reason on the
summary line, and SHALL carry a hidden `<!-- lgtmaybe-risk:<level> -->` marker
whose family is disjoint from the summary, finding, reviewed, incomplete and
lenses markers. `summary_template` SHALL accept a `{risk}` placeholder; a
template without it renders no visible verdict, but the marker is still carried.
A clean review's `LGTM` summary carries the verdict too. An unavailable
assessment SHALL render as `risk unavailable` and carry no marker, so a gate
reads its absence as unknown rather than as a level.

#### Scenario: default summary line
- **WHEN** a review finds three issues on a `high` change
- **THEN** the summary line reads with a `risk high` segment and its top reason, and the summary ends with `<!-- lgtmaybe-risk:high -->`

#### Scenario: custom template without the placeholder
- **WHEN** `summary_template` is `"{count} findings"`
- **THEN** the visible line shows no risk segment and the hidden marker is still present

### Requirement: The overview explains the verdict
<!-- anchor: risk.overview -->

When the change overview posts and risk is enabled, it SHALL carry a
`Risk of Change` section, headed with the level, placed directly above High
Impact Areas and listing every reason. The section MUST agree with the summary's
level for the same head. The local `lgtmaybe diagram` command SHALL print the
same section.

#### Scenario: overview with a high-risk change
- **WHEN** the overview posts for a `high` change with two reasons
- **THEN** a `Risk of Change: High` section above High Impact Areas lists both reasons

#### Scenario: overview stood down
- **WHEN** the PR already carries its own diagram and the overview is skipped
- **THEN** the verdict still appears in the review summary
