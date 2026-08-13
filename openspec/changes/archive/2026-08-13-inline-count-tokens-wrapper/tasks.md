## 1. Verification

- [x] 1.1 Confirm no test monkeypatches or imports `_count_tokens` and capture the existing prompt-cache test baseline.

## 2. Refactor

- [x] 2.1 Move the lazy `count_tokens` import into `_with_cache_control` and delete the delegating wrapper.
- [x] 2.2 Run provider prompt-cache tests and the spec-anchor suite.
