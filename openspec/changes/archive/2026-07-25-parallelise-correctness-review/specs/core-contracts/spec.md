## MODIFIED Requirements

### Requirement: Nine built-in lenses, provider-aware preset fan-out

`ReviewCategory` SHALL enumerate the nine built-in lenses. `ReviewPreset` SHALL
shape their fan-out: `fast` uses four focused calls when more than one worker is
available and three combined calls for a single-worker configuration; `full`
runs one call per selected built-in category.
<!-- anchor: core.lenses -->

#### Scenario: parallel-capable default
- **WHEN** a fast review has effective concurrency greater than one
- **THEN** correctness is split into two concurrent tasks without creating a
  new public review category

#### Scenario: serial default
- **WHEN** a fast review has effective concurrency of one
- **THEN** correctness remains one combined task
