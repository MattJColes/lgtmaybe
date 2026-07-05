"""The curated lens packs bundled in the package must be valid, loadable lenses.

These ship inside ``lgtmaybe/lenses/<pack>/`` and are enabled with
``lens_paths: [pack:<name>]``. They go straight into a model's system prompt, so
a malformed one would break a real review — this suite is the gate that keeps the
bundled packs honest: every file parses as a ``CustomLens``, ids are unique and
never collide with a built-in category, and every shipped lens carries a worked
example (which sharply improves smaller models).

The packs are located through ``importlib.resources`` — the package-resource
interface, the same view a ``pip install`` sees — so these tests exercise the
*packaged* data (and fail if the YAML is ever dropped from the wheel/sdist),
not merely the source-tree layout.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import yaml

from lgtmaybe.core.models import CustomLens, ReviewCategory

# Resolve via the package, not tests/../src, so an installed distribution is
# tested too: this points at site-packages/lgtmaybe/lenses when installed.
_LENSES_DIR = Path(str(files("lgtmaybe").joinpath("lenses")))
_BUILTIN_IDS = frozenset(c.value for c in ReviewCategory)


def _pack_files() -> list[Path]:
    return sorted(p for ext in ("*.yml", "*.yaml") for p in _LENSES_DIR.rglob(ext))


def _packs() -> list[str]:
    return sorted(p.name for p in _LENSES_DIR.iterdir() if p.is_dir())


def _lenses_in(file: Path) -> list[dict]:
    data = yaml.safe_load(file.read_text())
    return data if isinstance(data, list) else [data]


def test_lens_data_ships_in_the_package() -> None:
    """A known bundled lens is reachable through the package-resource API,
    so this fails if the YAML is excluded from the built wheel/sdist."""
    resource = files("lgtmaybe").joinpath("lenses", "design", "wrong-abstraction.yml")
    assert resource.is_file()
    assert _LENSES_DIR.is_dir()
    assert _LENSES_DIR.parent.name == "lgtmaybe"


def test_every_bundled_lens_is_valid() -> None:
    """Every shipped lens file parses as a CustomLens with a worked example."""
    files_ = _pack_files()
    assert files_, "no bundled lens files found"
    for file in files_:
        for item in _lenses_in(file):
            lens = CustomLens.model_validate(item)
            assert lens.example_diff and lens.example_finding, (
                f"{file.name}: bundled lenses must ship a worked example"
            )


def test_bundled_lens_ids_are_unique_and_dont_shadow_builtins() -> None:
    """No two bundled lenses share an id, and none collides with a built-in."""
    ids = [item["id"] for file in _pack_files() for item in _lenses_in(file)]
    assert len(ids) == len(set(ids)), f"duplicate bundled lens ids: {ids}"
    assert not (set(ids) & _BUILTIN_IDS), "a bundled lens id collides with a built-in category"


def test_each_pack_loads_through_the_real_loader(tmp_path):
    """`lens_paths: [pack:<name>]` resolves and validates end-to-end via the loader."""
    from lgtmaybe.config.loader import load_config

    packs = _packs()
    assert packs, "no bundled lens packs found"
    for pack in packs:
        cfg_file = tmp_path / ".lgtmaybe.yml"
        cfg_file.write_text(f"provider: ollama\nmodel: m\nlens_paths:\n  - pack:{pack}\n")
        cfg = load_config(config_path=cfg_file)
        assert cfg.extra_lenses, f"pack {pack!r} loaded no lenses"
