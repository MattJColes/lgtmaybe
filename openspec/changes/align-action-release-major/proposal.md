## Why

lgtmaybe 1.0.0 is published, but the v1 Action metadata still defaults to the
v0 container and the supplied workflows still pin `@v0`. The committed
release-please override also keeps proposing 1.0.0 instead of the next patch.
Users therefore miss fixes already merged to the v1 code line.

## What Changes

- Point the Action's default image and supplied workflow examples at major v1.
- Update the public setup documentation to recommend the v1 floating tag.
- Remove the consumed one-time `release-as: 1.0.0` override.
- Add a deterministic test that derives the expected image tag from the package
  version so the Action cannot silently drift across another major release.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-and-local`: Keep GitHub Action distribution metadata aligned with the
  package's released major version.

## Impact

The change affects only distribution metadata, supplied examples,
documentation, and its regression test. Runtime review behavior and provider
configuration are unchanged.
