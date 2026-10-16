"""A renamed docs page keeps its old URL working through a redirect stub.

The hook writes a small HTML page at each old URL on build. These tests pin the
map to real pages and the stub to a relative link, so it works wherever the
site is served.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "docs"))

from redirects import REDIRECTS, on_post_build  # noqa: E402

_DOCS = _ROOT / "docs"


def test_every_redirect_points_at_a_page_that_exists() -> None:
    for old, new in REDIRECTS.items():
        assert (_DOCS / new).is_file(), f"{old} redirects to missing page {new}"


def test_no_redirect_shadows_a_live_page() -> None:
    for old in REDIRECTS:
        assert not (_DOCS / old).exists(), f"{old} is still a page; drop its redirect"


def test_the_hook_is_registered_in_mkdocs_yml() -> None:
    config = (_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "- docs/redirects.py" in config


def test_writes_a_stub_at_the_old_directory_url(tmp_path: Path) -> None:
    on_post_build({"site_dir": str(tmp_path), "use_directory_urls": True})

    stub = tmp_path / "how-to" / "run-locally-with-ollama" / "index.html"
    html = stub.read_text(encoding="utf-8")
    assert 'content="0; url=../run-locally/"' in html
    assert 'href="../run-locally/"' in html


def test_writes_a_stub_at_the_old_file_url(tmp_path: Path) -> None:
    on_post_build({"site_dir": str(tmp_path), "use_directory_urls": False})

    stub = tmp_path / "how-to" / "run-locally-with-ollama.html"
    assert 'url=run-locally.html"' in stub.read_text(encoding="utf-8")
