## Context

A resolved discussion is history, not evidence that the same problem remains
actively reported.

## Decision

Use the opening note's existing `resolved` flag to skip the entire discussion
when collecting active finding ids.

## Non-Goals

- Rewrite historical GitLab comments.
- Change Gitea or GitHub deduplication.
