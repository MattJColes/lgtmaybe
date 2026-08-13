## Context

Click supplies group, command, and nested-command help through `--help`; the repository additionally maintains a 28-line alias.

## Goals / Non-Goals

**Goals:** Retain full help coverage using Click's native interface and remove the duplicate command tree walk.

**Non-Goals:** Redesign help text or command options.

## Decisions

Use `lgtmaybe --help`, `lgtmaybe review --help`, and nested `--help` forms directly. No replacement alias is added because that would preserve the duplicate surface.

## Risks / Trade-offs

- Existing users of the alias must change spelling → docs and smoke tests are updated in the same change.
