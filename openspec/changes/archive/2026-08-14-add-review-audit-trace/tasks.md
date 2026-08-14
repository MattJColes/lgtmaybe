## 1. Regression coverage

- [x] 1.1 Add a profile test that distinguishes valid zero findings, parse failure, and downstream removal

## 2. Finding-flow instrumentation

- [x] 2.1 Record parsed findings or parse errors on each review call
- [x] 2.2 Render per-call and parsed-versus-returned finding counts in the profile

## 3. Specification and verification

- [x] 3.1 Validate the review-pipeline delta spec for profile finding flow
- [x] 3.2 Run focused tests, the CLI runtime check, anchor tests, lint, and type checking
