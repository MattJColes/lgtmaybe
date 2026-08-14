## Why

Successful model calls can all parse as empty findings and still end in `LGTM`, while the existing profile cannot show whether findings were absent at the model boundary or removed later by reflection and filtering. Issue #440 exposed this with OpenRouter Claude Opus runs across a planted-defect corpus.

## What Changes

- Extend the opt-in `--profile` output with each review call's parsed finding count.
- Report the total findings parsed from review calls beside the number returned after the pipeline.
- Mark successful provider responses whose findings payload cannot be parsed as call errors in the same profile row.
- Keep ordinary review output and genuinely clean `LGTM` behavior unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `review-pipeline`: Make profile diagnostics distinguish model-empty responses, parse failures, and findings removed downstream.

## Impact

The review engine and profiler gain finding-count instrumentation; focused engine/profile tests and the review-pipeline anchor/spec are updated. No dependency, provider API, or default review behavior changes.
