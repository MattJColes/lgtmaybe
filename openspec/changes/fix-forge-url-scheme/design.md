## Context

Both self-hosted gateway constructors already accept a scheme, but the locator
does not carry one and the registry never supplies it.

## Decision

Use `urllib.parse.urlsplit`, keep HTTPS as the default for scheme-less inputs,
and pass the resulting scheme through the existing constructor parameters.

## Non-Goals

- Add new URL schemes.
- Change GitHub Enterprise support.
