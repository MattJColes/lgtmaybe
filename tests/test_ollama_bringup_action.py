"""The shared ollama bring-up composite action.

Two workflows need a live local model (e2e-local, rlm-bench) and used to carry
byte-identical bring-up steps. One composite action owns them now, so the two
cannot drift — the thing this suite is here to keep true.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.conftest import read_workflow

_ACTION = Path(__file__).parent.parent / ".github" / "actions" / "ollama-bringup" / "action.yml"
_USERS = ("e2e-local.yml", "rlm-bench.yml")


def _action() -> dict:
    return yaml.safe_load(_ACTION.read_text(encoding="utf-8"))


def test_the_action_takes_the_model_it_serves_as_an_input() -> None:
    action = _action()

    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["model"]["required"] is True
    assert "models-path" in action["inputs"]


def test_the_action_installs_uv_ollama_and_serves_the_model() -> None:
    steps = _action()["runs"]["steps"]
    uses = [step.get("uses", "") for step in steps]
    runs = "\n".join(str(step.get("run", "")) for step in steps)

    assert any(u.startswith("astral-sh/setup-uv@") for u in uses)
    assert any(u.startswith("actions/cache@") for u in uses)
    assert "uv sync --dev" in runs
    assert "ollama.com/install.sh" in runs
    # The same script DEVELOPMENT.md tells a developer to run, so CI's bring-up
    # cannot drift from a local one either.
    assert "scripts/e2e-up.sh ollama" in runs


def test_the_action_does_not_check_out_the_repository() -> None:
    """A local `uses: ./…` step only resolves once the workspace already holds
    the repo, so the checkout has to stay with the calling job."""
    uses = [step.get("uses", "") for step in _action()["runs"]["steps"]]

    assert not any(u.startswith("actions/checkout") for u in uses)


@pytest.mark.parametrize("workflow", _USERS)
def test_both_model_workflows_call_the_shared_action(workflow: str) -> None:
    _text, parsed = read_workflow(workflow)
    steps = [step for job in parsed["jobs"].values() for step in job["steps"]]
    uses = [step.get("uses", "") for step in steps]
    runs = "\n".join(str(step.get("run", "")) for step in steps)

    assert "./.github/actions/ollama-bringup" in uses
    assert any(u.startswith("actions/checkout") for u in uses), "the caller checks out"
    # The bring-up itself must live in one place only: no workflow may still
    # carry its own copy of the install/serve steps.
    assert "ollama.com/install.sh" not in runs
    assert "scripts/e2e-up.sh" not in runs
    assert not any(u.startswith("actions/cache@") for u in uses)
