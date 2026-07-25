## 1. Acceptance Test

- [x] 1.1 Extend the Action metadata test to require Marketplace-facing guidance that names workflow configuration and the `provider`, `model`, and authentication inputs; run it and confirm it fails before documentation changes.

## 2. Marketplace Onboarding

- [x] 2.1 Update `action.yml` descriptions so the Marketplace input list explains that provider and model are selected in the workflow `with:` block.
- [x] 2.2 Add a concise Marketplace setup section to the README with one complete example and a direct link to all provider workflow examples.
- [x] 2.3 Update the GitHub Action how-to so its opening explains the Marketplace limitation and the workflow-based selection step.
- [x] 2.4 Replace the separate GitHub App setup guide with a Marketplace Action clarification and remove it from the default navigation.

## 3. Verification

- [x] 3.1 Regenerate the derived `llms.txt` documentation after changing the how-to guide.
- [x] 3.2 Run the focused Action/documentation tests, the rendered documentation build, and `uv run pytest tests/specs -q`.
- [x] 3.3 Re-run the relevant spec anchors and confirm the provider factory anchor still resolves exactly once.
- [x] 3.4 Regenerate derived documentation and re-run the focused tests, documentation build, and spec checks after simplifying the App guidance.
