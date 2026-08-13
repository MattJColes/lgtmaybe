from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
ACTION_GUIDE = ROOT / "docs" / "how-to" / "use-as-github-action.md"
APP_GUIDE = ROOT / "docs" / "how-to" / "post-as-a-github-app.md"
RELEASING_GUIDE = ROOT / "docs" / "how-to" / "releasing.md"


def _public_app_example() -> str:
    guide = APP_GUIDE.read_text(encoding="utf-8")
    return guide.split("## Install the public lgtmaybe App", 1)[1].split(
        "## Use your own GitHub App", 1
    )[0]


def test_docs_present_action_only_and_lgtmaybe_bot_paths() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in (README, ACTION_GUIDE, APP_GUIDE)
    )

    assert "github-actions[bot]" in combined
    assert "lgtmaybe[bot]" in combined
    assert "https://github.com/apps/lgtmaybe/installations/new" in combined


def test_public_app_example_needs_oidc_but_no_private_key() -> None:
    example = _public_app_example()

    assert "id-token: write" in example
    assert "github_identity: lgtmaybe" in example
    assert "app_private_key" not in example
    assert "LGTMAYBE_APP_PRIVATE_KEY" not in example


def test_identity_choice_does_not_replace_provider_configuration() -> None:
    example = _public_app_example()

    for setting in ("provider:", "model:", "api_key:"):
        assert setting in example


def test_docs_explain_permissions_privacy_failure_and_uninstall() -> None:
    guide = APP_GUIDE.read_text(encoding="utf-8").casefold()

    for phrase in (
        "contents: read",
        "pull requests: write",
        "issues: write",
        "cannot be combined with `fail_on`",
        "never receives your diff",
        "never papered over",
        "uninstall",
    ):
        assert phrase in guide

    assert not re.search(r"copy (?:our|the lgtmaybe) private key", guide)


def test_maintainer_docs_explain_safe_private_key_rotation() -> None:
    guide = RELEASING_GUIDE.read_text(encoding="utf-8").casefold()
    rotation = guide.split("## rotate the public app private key", 1)[1]

    assert rotation.index("generate a new private key") < rotation.index("delete the old key")
    assert "lgtmaybe/github-app/private-key" in rotation
    assert "update-function-configuration" in rotation
    assert "smoke" in rotation
