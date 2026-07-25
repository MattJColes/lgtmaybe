## Context

lgtmaybe has no POSIX-only imports or shell-dependent subprocess calls, but it
is not Windows-clean at its trust boundaries. Locale-default text I/O can fail
on non-ASCII source, Windows paths do not join the forward-slash path namespace
used by GitHub diffs, the static-analysis subprocess environment omits Windows
runtime variables, gcloud ADC is probed in the Unix location, and read-only
temporary trees can leak. The current Ubuntu-only CI matrix cannot observe
these failures.

The release pipeline publishes PyPI, Homebrew, and GHCR artifacts from the
release-please workflow. A Windows executable and winget update must join that
same release graph because releases created with `GITHUB_TOKEN` do not emit a
second `release: published` event to other workflows.

## Goals / Non-Goals

**Goals:**

- Run the full test suite on the supported Python floor and executable Python
  version under real Windows locale, path, and filesystem semantics.
- Make all owned text boundaries deterministic UTF-8 without hiding malformed
  configuration, while tolerating undecodable output from external processes.
- Preserve forward-slash, case-sensitive git path semantics on every host.
- Preserve static-analysis isolation and ambient Vertex authentication on
  Windows.
- Publish a smoke-tested portable executable on every release and update the
  existing winget package from the second release onward.
- Deliver the work as two reviewable PRs: compatibility and CI first, packaging
  and distribution second.

**Non-Goals:**

- Bundle boto3, google-auth, or azure-identity in the executable; pip remains
  the installation path for keyless cloud authentication.
- Add semgrep support on Windows when upstream provides no Windows build.
- Replace the provider, engine, or Click CLI architecture.
- Automate the first moderated winget package submission.
- Build ARM64 Windows artifacts in this change.

## Decisions

### Add two focused Windows CI legs before compatibility fixes

The main test job will use an `include` matrix so Ubuntu retains Python
3.11-3.14 while Windows runs 3.11 and 3.13. Windows 3.11 protects the supported
floor and 3.13 matches the executable build; a Windows 3.14 leg adds little
coverage while litellm is constrained there. `PYTHONWARNDEFAULTENCODING=1`
turns implicit text encodings into a CI gate without `PYTHONUTF8=1` masking
cp1252 behavior. Windows disables `core.autocrlf` before checkout.

Alternative: a full OS-by-Python cross-product. Rejected because it doubles
expensive coverage without adding distinct boundary behavior.

### Fix encoding at the boundary and enforce it mechanically

Owned files use `encoding="utf-8"`. Text-mode subprocesses also use UTF-8 but
set `errors="replace"` because external output is untrusted and a decode error
must not erase an otherwise useful review. Config and files written by
lgtmaybe remain strict. A subprocess-driven quality test runs representative
call sites under `-X warn_default_encoding -W error::EncodingWarning`, and
pytest treats `EncodingWarning` as an error.

The Click group configures stdout and stderr once through a small,
exception-suppressed `reconfigure(encoding="utf-8", errors="replace")`
boundary. This avoids editing every emoji call site and remains compatible
with test streams that do not support reconfiguration.

Alternative: set `PYTHONUTF8=1`. Rejected because it masks missing boundary
declarations and does not control users' launch environments.

### Canonicalise repository paths at producers

Static-analysis tool paths pass through one `_posix_rel` helper that handles
absolute paths, `./`, and Windows-style backslashes even when tested on POSIX.
The ast-grep adapter emits `Path.as_posix()`. Consumers continue comparing the
canonical forward-slash strings they already receive. Git path globbing uses
`fnmatchcase`, because repository paths must not inherit host filesystem case
rules.

Alternative: normalise at every consumer. Rejected because multiple consumers
would duplicate the same guard and new consumers could silently regress.

### Preserve the Windows static-analysis sandbox explicitly

The scrubbed environment stays an exact minimal mapping on POSIX. On Windows it
passes through only process-critical `SystemRoot`, `COMSPEC`, `PATHEXT`, `TEMP`,
and `TMP`, while pinning `HOME`, `USERPROFILE`, `APPDATA`, and `LOCALAPPDATA` to
the analysis root. A patchable module-level `_WINDOWS` flag keeps both branches
testable on Linux without weakening the guarantee that cloud credentials do
not leak.

Alternative: inherit `os.environ` and remove known credential keys. Rejected
because denylisting cannot provide the existing sandbox guarantee.

### Use platform-native cleanup and credential locations

Temporary checkout cleanup retries after clearing read-only attributes. Python
3.12+ uses `shutil.rmtree(onexc=...)`; Python 3.11 uses `onerror=...` to avoid
the newer deprecation warning. `TemporaryDirectory` users opt into ignored
cleanup errors where static analysis is already best-effort. Vertex credential
probing checks `CLOUDSDK_CONFIG` first, then `%APPDATA%\gcloud` on Windows and
`~/.config/gcloud` elsewhere.

### Build one portable executable and gate it before publishing

PyInstaller builds a one-file console executable on `windows-latest` with
Python 3.13. The spec collects lgtmaybe and litellm data, includes tiktoken's
registry imports, and bundles ast-grep with a runtime hook that places the
temporary extraction directory on `PATH`. The workflow exercises `--help`,
`config path`, and `help review` before attaching a versioned x86_64 asset to
the existing GitHub release.

Alternative: onedir plus zip. Deferred unless measured one-file size or cold
start is unacceptable; winget supports either shape.

### Sequence release, executable, then winget

Both new workflows support `workflow_call` and recovery via
`workflow_dispatch`. release-please calls the executable workflow after the
release exists, then calls winget only after the asset exists. The winget job
polls the public asset and runs `wingetcreate update` with `WINGET_TOKEN`.
Maintainers perform the first `wingetcreate new` submission manually because
it establishes portable installer metadata and enters Microsoft moderation.

## Risks / Trade-offs

- [One-file size and extraction latency may be high] → Print artifact size in
  CI and retain onedir-plus-zip as the measured fallback.
- [PyInstaller misses a lazy litellm import] → Run real executable smoke
  commands before upload and widen hidden imports only when the gate proves it
  necessary.
- [First winget moderation takes days or weeks] → Document that delay and keep
  PyPI plus the GitHub release asset available immediately.
- [Read-only cleanup semantics differ by Python version] → Test the shared
  helper and keep the `onerror`/`onexc` split until Python 3.11 is dropped.
- [Bundled ast-grep increases artifact size] → Measure the release artifact;
  remove it only through an explicit quality-versus-size decision.

## Migration Plan

1. PR 1 adds the Windows CI legs as the first commit so existing failures are
   observable, then lands each test-first compatibility fix and targeted living
   spec update until all Linux and Windows checks pass.
2. PR 2 adds the PyInstaller acceptance test and spec, executable workflow,
   winget workflow, release graph wiring, docs, and distribution spec.
3. Dispatch the executable workflow against a current tag to validate the asset
   and smoke gate.
4. Create and moderate the first winget manifest manually, then dispatch the
   winget workflow to validate automated updates.

Rollback removes the two reusable release jobs without affecting PyPI,
Homebrew, or GHCR publishing. The compatibility fixes are host-neutral and do
not require data migration.

## Open Questions

- What measured executable size or cold-start time should trigger the
  onedir-plus-zip fallback? The workflow will expose the data before that
  decision is needed.
