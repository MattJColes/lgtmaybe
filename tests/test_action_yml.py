"""Structural guards for the composite ``action.yml``."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

from lgtmaybe.cli import action_inputs

_ACTION_YML = Path(__file__).parent.parent / "action.yml"
_PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
_RELEASE_PLEASE_CONFIG = Path(__file__).parent.parent / "release-please-config.json"
_README = Path(__file__).parent.parent / "README.md"
_GITHUB_APP_GUIDE = Path(__file__).parent.parent / "docs" / "how-to" / "post-as-a-github-app.md"
_MKDOCS = Path(__file__).parent.parent / "mkdocs.yml"

_TOKEN_EXPR = (
    "${{ steps.lgtmaybe-token.outputs.token || "
    "steps.app-token.outputs.token || inputs.github_token }}"
)


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


def _step(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id {step_id!r}")


def test_declares_github_app_inputs() -> None:
    inputs = _action()["inputs"]
    for name in ("app_id", "app_private_key", "app_owner", "app_repositories"):
        assert name in inputs, f"action.yml must declare the '{name}' input"


def test_declares_explicit_github_identity_inputs() -> None:
    inputs = _action()["inputs"]

    assert inputs["github_identity"]["default"] == "actions"
    assert inputs["identity_broker_url"]["default"].startswith("https://")


def test_optional_config_path_defaults_to_empty() -> None:
    assert _action()["inputs"]["config_path"]["default"] == ""


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

    action_section = (
        _README.read_text(encoding="utf-8")
        .split("## Use as a GitHub Action", maxsplit=1)[1]
        .split("## Distribution", maxsplit=1)[0]
    )
    assert "GitHub Marketplace" in action_section
    for input_name in ("provider:", "model:", "api_key:"):
        assert input_name in action_section


def test_marketplace_setup_explains_optional_branded_identity() -> None:
    guide = _GITHUB_APP_GUIDE.read_text(encoding="utf-8")
    assert "https://github.com/apps/lgtmaybe/installations/new" in guide
    assert "github_identity: lgtmaybe" in guide
    assert "id-token: write" in guide
    assert "Post as lgtmaybe[bot]" in _MKDOCS.read_text(encoding="utf-8")


def test_mint_step_is_pinned_and_gated_on_app_id() -> None:
    step = _mint_step()
    assert step["uses"] == "actions/create-github-app-token@v3", (
        "pin the mint action to a major tag, matching the other bundled actions"
    )
    assert "app_id" in str(step.get("if", "")), (
        "the mint step must be gated on the app_id input so it is a no-op by default"
    )
    with_ = step.get("with", {})
    assert with_.get("client-id") == "${{ inputs.app_id }}"
    assert "app-id" not in with_
    assert with_.get("private-key") == "${{ inputs.app_private_key }}"


def test_container_token_prefers_the_selected_app_token() -> None:
    assert _run_lgtmaybe_step()["env"]["GITHUB_TOKEN"] == _TOKEN_EXPR


def test_public_identity_exchange_is_gated_and_masks_the_token() -> None:
    step = _step("lgtmaybe-token")

    assert "github_identity" in str(step.get("if", ""))
    assert "github-app-identity.py" in step["run"]
    assert "exchange" in step["run"]


def test_public_identity_cleanup_is_always_run_without_public_output() -> None:
    action = _action()
    cleanup = _step("revoke-lgtmaybe-token")

    assert "always()" in str(cleanup.get("if", ""))
    assert "revoke" in cleanup["run"]
    assert "outputs" not in action


def test_identity_configuration_is_validated_before_minting() -> None:
    validation = _step("validate-identity")

    assert "GITHUB_IDENTITY" in validation["env"]
    assert "APP_ID" in validation["env"]
    assert "FAIL_ON" in validation["env"]
    assert "validate" in validation["run"]


def test_default_container_image_tracks_package_major() -> None:
    version = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    major = version.split(".", maxsplit=1)[0]

    assert _action()["inputs"]["image"]["default"] == f"ghcr.io/mattjcoles/lgtmaybe:v{major}"


def test_release_please_is_not_pinned_to_a_consumed_version() -> None:
    config = json.loads(_RELEASE_PLEASE_CONFIG.read_text(encoding="utf-8"))

    assert "release-as" not in config["packages"]["."]


# An action input reaches the container only if it is declared, mapped to an
# INPUT_* env var, AND read by `action_inputs()`. Miss either link and the input
# is silently dead — which is exactly how `max_tokens` was lost. These two tests
# pin the chain. (The third link, forwarding onto `docker run`, used to be a
# hand-written `-e INPUT_*` list; it is now an `--env-file` generated from the
# step's own environment, so there is no second copy left to drift.)
#
# Inputs consumed by the composite's own steps rather than the container: cloud
# auth (the OIDC/WIF steps export the provider SDKs' own credential vars), the
# App-identity inputs (consumed by the mint/exchange steps), and the two that
# map to differently-named vars (`github_token` → GITHUB_TOKEN, `image` →
# LGTMAYBE_IMAGE).
_NON_CONTAINER_INPUTS = frozenset(
    {
        "github_token",
        "image",
        "aws_role_arn",
        "aws_region",
        "gcp_wif_provider",
        "gcp_service_account",
        "azure_client_id",
        "azure_tenant_id",
        "github_identity",
        "identity_broker_url",
        "app_id",
        "app_private_key",
        "app_owner",
        "app_repositories",
    }
)


def _declared_input_env_names() -> set[str]:
    """``INPUT_*`` names for every action input meant to reach the container."""
    return {
        f"INPUT_{name.upper()}" for name in _action()["inputs"] if name not in _NON_CONTAINER_INPUTS
    }


def _env_block_input_names() -> set[str]:
    """``INPUT_*`` names the run step maps from inputs."""
    return {key for key in _run_lgtmaybe_step()["env"] if key.startswith("INPUT_")}


def test_every_declared_input_is_mapped_to_an_env_var() -> None:
    """A declared input with no INPUT_* mapping can never reach the container."""
    assert _declared_input_env_names() == _env_block_input_names()


def test_every_mapped_env_var_is_read_by_the_cli() -> None:
    """A mapped var nothing reads is dead weight; an unmapped read is a bug.

    The regression this chain exists for: INPUT_MAX_TOKENS was set in the step's
    `env:` block but never reached the container, so `max_tokens` set on the
    Action was silently dropped while its unit test (which sets the var
    directly) passed.
    """
    assert _env_block_input_names() == {f"INPUT_{key.upper()}" for key in action_inputs()}


def test_removed_answer_replies_input_is_absent() -> None:
    assert "answer_replies" not in _action()["inputs"]
    assert "INPUT_ANSWER_REPLIES" not in _run_lgtmaybe_step()["env"]
    assert "answer_replies" not in action_inputs()


def test_docker_run_forwards_the_step_environment_by_env_file() -> None:
    """The env file is generated from the step's own env, not a second name list.

    A bare `VAR` line takes its value from the ambient environment, so the
    INPUT_* names are written exactly once (the `env:` block). Anything that
    reintroduces a hand-maintained `-e INPUT_*` list re-opens the drift.
    """
    run = _run_lgtmaybe_step()["run"]

    assert "compgen -e" in run
    assert '--env-file "${RUNNER_TEMP}/lgtmaybe.env"' in run
    assert "GITHUB_SERVER_URL" in run
    assert "GITHUB_API_URL" in run
    assert not re.search(r"-e\s+INPUT_[A-Z0-9_]+", run)
