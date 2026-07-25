## Why

The main CI workflow repeats the full gate six times across Python versions and
operating systems, consuming GitHub Actions minutes without proportionate
confidence. Running the gate on the minimum supported Python version preserves
the strictest compatibility boundary while cutting four duplicate jobs.

## What Changes

- Run the full CI gate on Python 3.11 only.
- Retain one Ubuntu job and one Windows job so platform-specific behavior stays
  covered.
- Stop running the routine pull-request gate on Python 3.12, 3.13, and 3.14.
- Keep the stable, matrix-independent `check` job used by branch protection.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `windows-distribution`: Require the main CI gate on the minimum supported
  Python version for both Ubuntu and Windows instead of every supported Python
  version on Ubuntu and two Python versions on Windows.

## Impact

The change affects `.github/workflows/ci.yml`, its workflow contract test, the
anchored `windows-distribution` living spec, and the CI matrix description in
`CLAUDE.md`. It does not change the package's supported Python range,
dependencies, runtime behavior, or release workflows.
