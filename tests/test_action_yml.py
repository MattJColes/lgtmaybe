"""Structural guard for the composite ``action.yml``.

The GitHub App branded-bot path lives entirely in ``action.yml``: it mints an
installation token with ``actions/create-github-app-token`` (gated on the
``app_id`` input, the same shape as the three keyless-cloud auth steps) and
forwards it in the ``GITHUB_TOKEN`` the container already reads — preferring the
minted token and falling back to the default workflow token when the mint step
is skipped. There is no Python behaviour change, so this test pins that wiring so
a refactor of the YAML can't silently drop the fallback or the gate.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ACTION_YML = Path(__file__).parent.parent / "action.yml"
_README = Path(__file__).parent.parent / "README.md"

# The container reads GITHUB_TOKEN; prefer the minted App token, else the default
# workflow token. A skipped mint step yields an empty output, so ``||`` falls
# through to the pass-through ``github_token`` input.
_TOKEN_EXPR = "${{ steps.app-token.outputs.token || inputs.github_token }}"


def _action() -> dict:
    return yaml.safe_load(_ACTION_YML.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _action()["runs"]["steps"]


def _run_lgtmaybe_step() -> dict:
    for step in _steps():
        if isinstance(step.get("env"), dict) and "GITHUB_TOKEN" in step["env"]:
            return step
    raise AssertionError("no step sets GITHUB_TOKEN for the container")


def _mint_step() -> dict:
    for step in _steps():
        if str(step.get("uses", "")).startswith("actions/create-github-app-token"):
            return step
    raise AssertionError("no actions/create-github-app-token step")


def test_declares_github_app_inputs() -> None:
    inputs = _action()["inputs"]
    for name in ("app_id", "app_private_key", "app_owner", "app_repositories"):
        assert name in inputs, f"action.yml must declare the '{name}' input"


def test_marketplace_setup_explains_workflow_configuration() -> None:
    action = _action()
    marketplace_copy = " ".join(
        [
            action["description"],
            action["inputs"]["provider"]["description"],
            action["inputs"]["model"]["description"],
            action["inputs"]["api_key"]["description"],
        ]
    ).lower()
    for term in ("workflow", "provider", "model", "api key"):
        assert term in marketplace_copy

    action_section = _README.read_text(encoding="utf-8").split(
        "## Use as a GitHub Action", maxsplit=1
    )[1].split("## Distribution", maxsplit=1)[0]
    assert "GitHub Marketplace" in action_section
    for input_name in ("provider:", "model:", "api_key:"):
        assert input_name in action_section


def test_mint_step_is_pinned_and_gated_on_app_id() -> None:
    step = _mint_step()
    assert step["uses"] == "actions/create-github-app-token@v2", (
        "pin the mint action to a major tag, matching the other bundled actions"
    )
    assert "app_id" in str(step.get("if", "")), (
        "the mint step must be gated on the app_id input so it is a no-op by default"
    )
    with_ = step.get("with", {})
    assert with_.get("app-id") == "${{ inputs.app_id }}"
    assert with_.get("private-key") == "${{ inputs.app_private_key }}"


def test_container_token_prefers_the_minted_app_token() -> None:
    assert _run_lgtmaybe_step()["env"]["GITHUB_TOKEN"] == _TOKEN_EXPR
