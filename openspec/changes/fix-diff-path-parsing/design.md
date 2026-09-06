## Context

An unquoted `diff --git a/<old> b/<new>` line has no unique delimiter when a
path contains the same bytes. The later old/new metadata lines have explicit
prefixes and an optional tab-delimited timestamp.

## Decision

Split patches on `diff --git` boundaries, then read the new path from `+++ b/`
or the old path from `--- a/` for deletions. The diff walker follows the same
metadata.

## Non-Goals

- Decode Git's C-quoted control-character paths.
- Change hunk line-number handling.
