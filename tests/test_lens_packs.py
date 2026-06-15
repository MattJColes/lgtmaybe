"""The curated lens packs bundled in the package must be valid, loadable lenses.

These ship inside ``src/lgtmaybe/lenses/<pack>/`` and are enabled with
``lens_paths: [pack:<name>]``. They go straight into a model's system prompt, so
a malformed one would break a real review — this suite is the gate that keeps the
bundled packs honest: every file parses as a ``CustomLens``, ids are unique and
never collide with a built-in category, and every shipped lens carries a worked
example (which sharply improves smaller models).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lgtmaybe.core.models import CustomLens, ReviewCategory

_LENSES_DIR = Path(__file__).resolve().parent.parent / "src" / "lgtmaybe" / "lenses"
_BUILTIN_IDS = frozenset(c.value for c in ReviewCategory)


def _pack_files() -> list[Path]:
    return sorted(p for ext in ("*.yml", "*.yaml") for p in _LENSES_DIR.rglob(ext))


def test_lenses_dir_is_inside_the_package() -> None:
    """The packs live under the wheel package so a pip install ships them."""
    assert _LENSES_DIR.is_dir()
    assert _LENSES_DIR.parent.name == "lgtmaybe"


def test_every_bundled_lens_is_valid() -> None:
    """Every shipped lens file parses as a CustomLens with a worked example."""
    files = _pack_files()
    assert files, "no bundled lens files found"
    for file in files:
        data = yaml.safe_load(file.read_text())
        items = data if isinstance(data, list) else [data]
        for item in items:
            lens = CustomLens.model_validate(item)
            assert lens.example_diff and lens.example_finding, (
                f"{file.name}: bundled lenses must ship a worked example"
            )


def test_bundled_lens_ids_are_unique_and_dont_shadow_builtins() -> None:
    """No two bundled lenses share an id, and none collides with a built-in."""
    ids: list[str] = []
    for file in _pack_files():
        data = yaml.safe_load(file.read_text())
        items = data if isinstance(data, list) else [data]
        ids.extend(item["id"] for item in items)
    assert len(ids) == len(set(ids)), f"duplicate bundled lens ids: {ids}"
    assert not (set(ids) & _BUILTIN_IDS), "a bundled lens id collides with a built-in category"


@pytest.mark.parametrize("pack", [p.name for p in _LENSES_DIR.iterdir() if p.is_dir()])
def test_each_pack_loads_through_the_real_loader(pack: str) -> None:
    """`lens_paths: [pack:<name>]` resolves and validates end-to-end via the loader."""
    import io

    from lgtmaybe.config.loader import load_config

    cfg = load_config(
        config_stream=io.StringIO(f"provider: ollama\nmodel: m\nlens_paths:\n  - pack:{pack}\n")
    )
    assert cfg.extra_lenses, f"pack {pack!r} loaded no lenses"
