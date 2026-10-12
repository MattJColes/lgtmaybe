## Why
GitHub freezes a review's native commit while lgtmaybe edits its summary across heads. A completed cached run can therefore have no current-head native artifact, and a forced clean rerun can rewrite identical bytes.
## What Changes
Create a native summary at the actually reviewed commit when that commit differs; retain old summaries and existing inline dedupe. Reuse the latest matching provider/model summary.
## Capabilities
### Modified Capabilities
- `github-posting`: summaries remain bound to their native reviewed commit.
## Impact
GitHub REST adapter, focused HTTP tests and its living posting requirement. No provider, dependency or merge-gate changes.
