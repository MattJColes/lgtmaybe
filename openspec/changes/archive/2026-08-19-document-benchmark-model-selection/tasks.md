## 1. Benchmark evidence

- [x] 1.1 Extract the current comparable breadth and long-horizon results from the benchmark repository and record the comparison keys, provisional state, and source commit.
- [x] 1.2 Add a documentation acceptance check that requires the model-selection guide to be linked from both the README and MkDocs navigation.

## 2. Documentation

- [x] 2.1 Add the model-selection guide with a balanced default, priority-based alternatives, metric definitions, and reproducibility caveats.
- [x] 2.2 Replace the README's generic model advice with the benchmark-backed recommendation and link the guide from the documentation index.
- [x] 2.3 Add the guide to `mkdocs.yml` and the generated LLM documentation index inputs as required by the existing docs workflow.

## 3. Verification

- [x] 3.1 Run the focused documentation acceptance check, regenerate derived docs if needed, and build the MkDocs site.
- [x] 3.2 Run OpenSpec/spec-anchor validation and inspect the final diff for stale or cross-suite claims.

## 4. Cloud and local decision paths

- [x] 4.1 Extend the documentation acceptance check to require separate cloud and local model guidance.
- [x] 4.2 Restructure the guide and README around a cloud-versus-local decision without naming a universal default.
- [x] 4.3 Regenerate derived docs and repeat documentation, site, OpenSpec, and spec-drift validation.

## 5. Plain-language edit

- [x] 5.1 Rewrite the guide and README summary in direct technical prose without changing the benchmark claims.
- [x] 5.2 Regenerate derived docs and rerun documentation and OpenSpec validation.
