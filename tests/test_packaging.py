"""Packaging guard: the keyless-cloud providers must ship their auth deps.

litellm normalises every provider to one ``completion()`` call, but it does
*not* pull the cloud SDKs needed to sign/authenticate the keyless paths — those
live behind litellm's own ``proxy``/``google`` extras. So each keyless-cloud
provider needs an extra here, and the Action image must bundle all of them, or
a review silently dies at call time with ``ModuleNotFoundError`` (e.g. "Missing
boto3 to call bedrock"). This test pins both halves so that regression can't
recur.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_PYINSTALLER_SPEC = _REPO_ROOT / "packaging" / "pyinstaller" / "lgtmaybe.spec"

# provider extra -> the import-name of the package its keyless path needs.
_CLOUD_EXTRAS = {
    "azure": "azure-identity",
    "bedrock": "boto3",
    "vertex": "google-auth",
}


def _optional_dependencies() -> dict[str, list[str]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def test_each_keyless_cloud_provider_has_an_extra() -> None:
    extras = _optional_dependencies()
    for extra, package in _CLOUD_EXTRAS.items():
        assert extra in extras, f"missing optional-dependency extra '{extra}'"
        names = [req.split(">")[0].split("=")[0].split("[")[0].strip() for req in extras[extra]]
        assert package in names, (
            f"extra '{extra}' must pull '{package}' so the keyless path can authenticate; "
            f"got {names}"
        )


def test_dockerfile_bundles_every_cloud_extra() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    for extra in _CLOUD_EXTRAS:
        assert f"--extra {extra}" in dockerfile, (
            f"Dockerfile must `uv sync ... --extra {extra}` so the image can run "
            f"keyless {extra} reviews"
        )


def test_pyinstaller_spec_collects_data_trees() -> None:
    spec = _PYINSTALLER_SPEC.read_text(encoding="utf-8")

    assert 'collect_data_files("lgtmaybe")' in spec
    assert 'collect_data_files("litellm")' in spec
    assert '"tiktoken_ext"' in spec
    assert '"tiktoken_ext.openai_public"' in spec
