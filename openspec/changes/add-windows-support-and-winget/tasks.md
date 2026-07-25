## 1. PR 1 - Windows CI first

- [x] 1.1 Re-run the `engine.static-analysis`, `engine.path-filters`, and `provider.credentials` anchors against the files PR 1 will touch and confirm each resolves exactly once.
- [x] 1.2 Add Windows 3.11 and 3.13 `include` entries to `.github/workflows/ci.yml`, set `PYTHONWARNDEFAULTENCODING=1`, disable `core.autocrlf` before checkout on Windows, and retain the existing `check` fan-in.
- [x] 1.3 Land the CI matrix as PR 1's first conventional commit so the compatibility failures are observable before their fixes.

## 2. PR 1 - UTF-8 boundaries

- [x] 2.1 Add the subprocess-based `test_no_default_encoding_io` quality gate and configure pytest to fail on `EncodingWarning`; run it red against the current implicit call sites.
- [x] 2.2 Add explicit UTF-8 to owned `read_text`/`write_text` calls and UTF-8 with replacement only to external subprocess output in static analysis, boundaries, config, CLI commands, local git, and ast-grep; run the encoding gate green.
- [x] 2.3 Add and run red-to-green byte-level tests proving non-Latin config values round-trip as UTF-8 and the static-analysis corpus is written as UTF-8.
- [x] 2.4 Add and run a red-to-green `_utf8_stdio` CLI boundary test using a cp1252 `TextIOWrapper`, then invoke the guarded stream reconfiguration from the root Click group.

## 3. PR 1 - Path, sandbox, credentials, and cleanup

- [x] 3.1 Add failing coverage for Windows, dotted, and POSIX analyzer paths, implement one `_posix_rel` producer helper, and make ast-grep emit forward-slash paths.
- [x] 3.2 Add cross-host case-sensitivity regression tests for review path filters and generated-file skip globs, then replace host-normalising glob matches with `fnmatchcase`.
- [x] 3.3 Add Linux-runnable tests for both `_scrubbed_env` branches, then pass through only required Windows process variables and pin all Windows user/profile directories to the analysis root while preserving the exact POSIX environment.
- [x] 3.4 Add failing Windows ADC location and override-precedence tests, then resolve Vertex ADC from `CLOUDSDK_CONFIG`, `%APPDATA%\gcloud`, or `~/.config/gcloud` in that order by platform.
- [x] 3.5 Add failing read-only tree cleanup coverage, implement the Python 3.11 `onerror` and Python 3.12+ `onexc` cleanup helper, and replace both silent checkout cleanup calls.
- [x] 3.6 Assert `ignore_cleanup_errors=True` at the best-effort `TemporaryDirectory` boundaries in static analysis and boundaries, then make the tests green.
- [x] 3.7 Resolve any Windows-only line-ending fixture failures without weakening format checks, preferring a repository-wide LF rule if the CI leg proves one is needed.

## 4. PR 1 - Specs and verification

- [x] 4.1 Update the `cli-and-local`, `review-pipeline`, and `provider-gateway` living requirements plus anchor sidecars for UTF-8 boundaries, case-sensitive path filters, Windows static-analysis isolation, and Windows gcloud ADC discovery.
- [x] 4.2 Run the focused new tests, full pytest suite, ruff check, ruff format check, mypy, living-spec tests, OpenSpec spec validation, and the spec drift check.
- [ ] 4.3 Confirm both Windows CI legs pass under locale defaults, then prepare PR 1 with a conventional title and no packaging/distribution changes.

## 5. PR 2 - Portable executable

- [ ] 5.1 Add a failing packaging test that requires lgtmaybe and litellm data collection plus tiktoken registry hidden imports in the PyInstaller spec.
- [ ] 5.2 Add the packaging dependency group and lockfile update for PyInstaller, then create the executable entrypoint, one-file spec, ast-grep binary collection, and runtime PATH hook needed to satisfy the test.
- [ ] 5.3 Add reusable and dispatchable `.github/workflows/windows-exe.yml` automation that normalises the version, skips an existing asset unless forced, checks out the tag, builds with Python 3.13, and smoke-tests `--help`, `config path`, and `help review` before upload.
- [ ] 5.4 Name and attach the versioned x86_64 executable to the existing GitHub release and print its measured size in the smoke job.

## 6. PR 2 - winget release chain

- [ ] 6.1 Add reusable and dispatchable `.github/workflows/winget.yml` automation that normalises the version, polls for the executable asset, and runs `wingetcreate update MattJColes.lgtmaybe` with `WINGET_TOKEN`.
- [ ] 6.2 Wire release-please to call the executable workflow after release creation and the winget workflow only after the executable succeeds, preserving the existing PyPI, Homebrew, and GHCR release jobs.
- [ ] 6.3 Add workflow-structure tests that assert the executable smoke gate, release sequencing, asset URL, portable package ID, and secret wiring.

## 7. PR 2 - Documentation, specs, and verification

- [ ] 7.1 Document `winget install MattJColes.lgtmaybe`, pip-on-Windows support, bundled ast-grep, and the executable's excluded cloud extras in the CLI installation guide and README.
- [ ] 7.2 Document the one-time winget fork, classic `public_repo` PAT, `WINGET_TOKEN`, initial portable manifest submission, and moderation expectations beside the existing release setup.
- [ ] 7.3 Update `CLAUDE.md` with the Windows distribution variant and CI legs, and add the `windows-distribution` living spec with exact-one-match anchors for its CI, executable, and winget requirements.
- [ ] 7.4 Run packaging and workflow tests, the full local quality gate, living-spec tests, OpenSpec spec validation, and the spec drift check.
- [ ] 7.5 Dispatch the executable workflow against a current tag and verify the smoke-tested asset; after the first winget manifest is moderated, dispatch the winget workflow and verify its update PR.
- [ ] 7.6 Prepare PR 2 with a conventional title, measured executable size noted, and no unresolved release-gate failures.
