## 1. Regression Tests

- [x] 1.1 Add a failing Action test proving `pull_request_review_comment` exits successfully before config, provider, engine, or GitHub setup.
- [x] 1.2 Add failing contract/workflow tests proving `answer_replies` and the shipped review-comment triggers are absent.

## 2. Runtime Removal

- [x] 2.1 Move the stale-event no-op to the start of the Action entrypoint and delete the automatic reply handler and model prompt.
- [x] 2.2 Delete reply-only injection and inbound thread-lookup code while preserving the GraphQL reply used by resolve-on-fix.
- [x] 2.3 Remove `answer_replies` from the typed config and Action input surface.

## 3. Workflows, Specs, and Documentation

- [x] 3.1 Remove review-comment triggers/guards from starter workflows and update workflow safety tests.
- [x] 3.2 Apply the targeted living-spec and anchor changes for CLI, config, and GitHub posting behavior.
- [x] 3.3 Remove reply guidance, document the breaking migration, and regenerate schema/reference/documentation outputs.

## 4. Verification

- [x] 4.1 Run focused Action, workflow, config, gateway, resolve-on-fix, docs, and spec-anchor tests.
- [x] 4.2 Run the full project test, lint, type, security, OpenSpec, and runtime smoke checks.
