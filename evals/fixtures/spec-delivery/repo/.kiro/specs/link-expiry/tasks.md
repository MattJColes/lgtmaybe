# Tasks: Payment link expiry

Derived from requirements.md. Each task names the file it changes and the
requirement it satisfies, so a reviewer can check delivery without reading the
design document.

## Conventions

- Tick a task only when the change that delivers it is in the same PR.
- A task that spans files lists each of them.
- Sub-bullets are notes, not separate tasks.

## Phase 1 — persistence

- [ ] 1.1 Add an `expires_at` column to the links table
  - _Requirements: 1.1_
- [ ] 1.2 Set the 30-day expiry when creating a link in src/links/service.py
  - _Requirements: 1.1_

## Phase 2 — redemption

- [ ] 1.3 Reject redemption of an expired link in src/links/service.py
  - _Requirements: 1.2_
- [ ] 1.4 Record an audit entry when a redemption is rejected as expired
  - _Requirements: 2.1_
