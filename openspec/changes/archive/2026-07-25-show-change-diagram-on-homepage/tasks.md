## 1. Homepage example

- [x] 1.1 Add a focused docs test asserting that the homepage contains a C4
  Mermaid example and links to the change-diagram guide; run it and confirm it
  fails before the homepage edit.
- [x] 1.2 Add the existing Redis/User API Mermaid example to `docs/index.md`
  beside the `/diagram` introduction.

## 2. Verification

- [x] 2.1 Run the focused docs test and the MkDocs strict build.
- [x] 2.2 Run `uv run pytest tests/specs -q` and confirm OpenSpec anchor hygiene.
