# stacked-pull-requests Specification

## Purpose

How lgtmaybe reviews a pull request that is one layer of a stack, each layer
built on the one below and merged bottom up: the GitHub gateway reads where the
PR sits, the engine tells every lens, and the review neither repeats itself on
a rebase that changes nothing nor misses a retarget that changes the diff.

## Requirements

### Requirement: The GitHub gateway reads a pull request's place in its stack

The GitHub gateway SHALL fill `PRContext.stack` from GitHub's native `stack`
object and stacks API, or else from the chain of open same-repository pull
requests each based on the branch of the one below, naming at most eight other
layers each side; any failure leaves it None and the review continues.
<!-- anchor: stack.context -->

#### Scenario: a native stack
- **WHEN** the pull request carries a `stack` object
- **THEN** its position, size and base come from that object, and each other
  open layer is named with its title and changed files

#### Scenario: a chain of branches
- **WHEN** there is no `stack` object and other open pull requests chain to
  this one by base and head branch
- **THEN** the walk finds them, stopping at the default branch, an ambiguous
  step, a branch already seen or the layer cap
- **AND** a walk that stopped at anything but a natural end (the default
  branch, or a step with no match) marks the stack incomplete

#### Scenario: a fork or a failed lookup
- **WHEN** the head branch lives in a fork, or a stack lookup fails
- **THEN** `stack` is None and the review runs as for a standalone pull request

### Requirement: Every lens is told where a stacked pull request sits

The engine SHALL put a redacted, neutralised STACK block naming the layer's
position and the other layers on the shared prefix of every review call, and
SHALL name the layer in the summary line (exact only for a complete stack, else
a lower bound); a pull request outside a stack MUST send its old prompt.
<!-- anchor: stack.prompt-block -->

#### Scenario: a hostile layer title
- **WHEN** another layer's title or path carries a forged block delimiter or a
  secret
- **THEN** the delimiter is neutralised and the secret redacted before the
  block reaches a model

#### Scenario: no stack
- **WHEN** `PRContext.stack` is None
- **THEN** no STACK block, preamble or summary segment is added

### Requirement: A rebase that leaves the diff unchanged is not reviewed again

An incremental run SHALL skip, posting nothing, when the whole pull request's
diff fingerprints the same as the completed head's did; the fingerprint drops
only `index` lines and hunk line ranges, and is trusted only when it was
stamped with that completed head.
<!-- anchor: stack.unchanged-rebase -->

#### Scenario: a lower layer merges
- **WHEN** GitHub rebases a layer onto its merged parent and the layer's edits
  are unchanged
- **THEN** the run reports that the diff is unchanged and makes no model call

#### Scenario: the rebase changes what a reviewer reads
- **WHEN** an added, removed or surrounding line differs after the rebase
- **THEN** the layer is reviewed as before

### Requirement: A retargeted pull request is reviewed in full

The action SHALL review a pull request in full, whatever `incremental` says, and
refresh its overview, on an `edited` event whose `changes` include `base`, and
SHALL review nothing on any other `edited` event; the supplied workflows MUST
subscribe to `edited` without letting such an edit cancel a running review.
<!-- anchor: stack.retarget -->

#### Scenario: a parent merges under a branch-chained stack
- **WHEN** GitHub moves the pull request onto its parent's base
- **THEN** a full review runs, because the head is unchanged and an incremental
  run would see nothing to do

#### Scenario: a title edit
- **WHEN** only the title or description changes
- **THEN** the workflow starts no job and the action posts nothing
