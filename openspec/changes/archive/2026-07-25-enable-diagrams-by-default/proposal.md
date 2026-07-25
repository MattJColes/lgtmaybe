## Why

The diagram feature is implemented but the repository's own workflow and the
documented starter workflows do not enable it, so users reasonably see no
diagrams on newly opened pull requests.

## What Changes

- Enable `auto_diagram` in lgtmaybe's dogfood workflow.
- Enable `auto_diagram` in the documented starter workflows used by new
  repositories.
- Verify the workflow examples consistently opt in without changing the
  Action input's backwards-compatible default.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Document that supplied starter workflows opt in to automatic
  diagrams for newly opened or reopened pull requests.

## Impact

The change affects GitHub Actions workflow examples and setup documentation
only. Existing installations remain unchanged unless their workflow opts in,
and no dependencies or public APIs change.
