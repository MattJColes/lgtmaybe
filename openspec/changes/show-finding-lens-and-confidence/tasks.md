## 1. Acceptance Tests

- [x] 1.1 Add a failing test that an inline comment's title line renders
  `**[MEDIUM · security · 8/10] Title**`.
- [x] 1.2 Add failing tests that the confidence half is omitted when unscored,
  and that `0` still renders (the falsy-vs-None trap).
- [x] 1.3 Add a test that an uncategorised finding's body is byte-identical to
  the pre-badge rendering.
- [x] 1.4 Add failing tests that demoted and broad findings carry the same badge.
- [x] 1.5 Add tests that the hidden fingerprint/identity markers are byte-identical
  with and without a badge, and that a comment posted before badges existed still
  dedupes (no re-post storm on the first run after upgrade).

## 2. Rendering

- [x] 2.1 Add `_finding_badge(f)` returning `""` / `" · <lens>"` /
  `" · <lens> · <n>/10"`, defanging the lens id.
- [x] 2.2 Render it inside the severity brackets on the inline comment title,
  `_render_demoted`, and `_render_broad`.

## 3. Specs & Docs

- [x] 3.1 Add the `github.finding-badge` requirement to
  `openspec/specs/github-posting/spec.md` and its ast-grep rule to `anchors.yml`.
- [x] 3.2 Document what a posted comment looks like in
  `docs/explanation/what-gets-reviewed.md`, including that an inline comment's
  badge is frozen at first post while the body's is rewritten each run.
- [x] 3.3 Note the badge beside `min_confidence` in
  `docs/how-to/configure-lgtmaybe-yml.md`, then regenerate `llms.txt`.

## 4. Verification

- [x] 4.1 Run the gateway posting suite.
- [x] 4.2 Run the full gate: pytest, ruff format/check, mypy, spec tests, and
  `openspec validate --specs`.
