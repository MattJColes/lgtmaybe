## Context

The composite Action launches the image named by its `image` input. That input
is a literal major tag, while the package major is stored separately in
`pyproject.toml`. The v1 release advanced the package and published a v1 image,
but left the Action default and copy-paste workflows on v0. A one-time
release-please `release-as` override also remained after 1.0.0 was cut.

## Goals / Non-Goals

**Goals:**

- Make `uses: ...@v1` launch the v1 image by default.
- Make supplied workflows and documentation adopt the supported v1 line.
- Let release-please calculate the next patch normally.
- Fail CI if the package major and default Action image diverge again.

**Non-Goals:**

- Change review concurrency, timeouts, or provider behavior.
- Remove the supported v0 image or floating tags.
- Automate edits to every major-version reference during a future major bump.

## Decisions

1. Keep a literal floating-major image in `action.yml`. GitHub Action metadata
   cannot derive its input default from `pyproject.toml`, and a floating major
   is the existing compatibility contract.
2. Derive the expected major in the test. This leaves the runtime metadata
   simple while making future drift an immediate deterministic failure.
3. Move starter workflows and current documentation to `@v1`; users explicitly
   pinned to v0 remain unaffected.
4. Delete `release-as` after its one-time use so conventional commits drive
   subsequent releases.

## Risks / Trade-offs

- [A future major bump still needs a small metadata edit] -> The alignment test
  fails in the same change that bumps `pyproject.toml`.
- [Existing v0 users do not receive this fix automatically] -> Preserve v0 as a
  compatibility line and move all maintained onboarding surfaces to v1.
