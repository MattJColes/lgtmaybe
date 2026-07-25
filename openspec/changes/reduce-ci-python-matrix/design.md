## Context

The main `test` job currently expands to four Ubuntu Python versions and two
Windows Python versions. Every leg installs the same dependencies and runs the
same lock, lint, format, type, and pytest commands. Python 3.11 is the declared
package minimum and therefore the compatibility boundary most likely to expose
use of unsupported newer syntax or APIs.

## Goals / Non-Goals

**Goals:**

- Reduce the routine full-suite matrix from six jobs to two.
- Keep Ubuntu and Windows behavior covered.
- Exercise the minimum supported Python version.
- Preserve the stable `check` job used by branch protection.

**Non-Goals:**

- Change the supported Python range.
- Add a scheduled compatibility matrix or new workflow.
- Change release, executable, or dependency-audit jobs.

## Decisions

- Keep an operating-system matrix with `ubuntu-latest` and `windows-latest`,
  but set the test job's Python version directly to `3.11`. This expresses the
  two intended jobs without a redundant one-value Python matrix.
- Run the complete existing gate on both jobs. Splitting lint and tests into
  separate workflows would add configuration and would not reduce the number of
  platform test runs requested here.
- Add a workflow contract test that asserts the two operating systems and the
  fixed minimum Python version before changing the workflow.
- Retain the stable `check` aggregation job unchanged apart from its stale
  explanatory comment.

## Risks / Trade-offs

- Newer-Python-only incompatibilities will no longer be caught by the routine
  test workflow. This is an accepted trade-off for lower Actions usage; the
  declared minimum remains the strongest syntax and standard-library boundary.
- Windows remains more expensive than Ubuntu, but removing it would lose direct
  path, encoding, and line-ending coverage for a supported distribution.
