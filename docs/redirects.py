"""mkdocs hook: keep a renamed page's old URL working.

Each entry in ``REDIRECTS`` maps an old source path to the page that replaced
it. After the build, a small HTML stub is written at the old URL that sends the
reader on with a relative link, so it works on any host and under any base path.

A hook rather than the mkdocs-redirects plugin, which now pulls a second copy
of mkdocs into the docs environment for a handful of stubs. Kept free of mkdocs
imports so it is testable from the dev environment.
"""

from __future__ import annotations

import html
import posixpath
from pathlib import Path
from typing import Any

# Old docs path -> new docs path, both relative to docs/.
REDIRECTS: dict[str, str] = {
    "how-to/run-locally-with-ollama.md": "how-to/run-locally.md",
}

_STUB = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Moved</title>
<link rel="canonical" href="{url}">
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={url}">
</head>
<body>
<p>This page has moved to <a href="{url}">{url}</a>.</p>
</body>
</html>
"""


def _output_path(page: str, directory_urls: bool) -> str:
    """Where mkdocs writes *page*, relative to the site root."""
    stem = page.removesuffix(".md")
    if directory_urls and posixpath.basename(stem) != "index":
        return f"{stem}/index.html"
    return f"{stem}.html"


def _link(old: str, new: str, directory_urls: bool) -> str:
    """The relative URL from *old*'s output file to *new*'s page."""
    source = _output_path(old, directory_urls)
    target = _output_path(new, directory_urls)
    rel = posixpath.relpath(target, posixpath.dirname(source))
    if directory_urls and rel.endswith("index.html"):
        rel = rel.removesuffix("index.html")
    return rel


def on_post_build(config: Any, **_: Any) -> None:
    directory_urls = bool(config["use_directory_urls"])
    site = Path(config["site_dir"])
    for old, new in REDIRECTS.items():
        stub = site / _output_path(old, directory_urls)
        stub.parent.mkdir(parents=True, exist_ok=True)
        url = html.escape(_link(old, new, directory_urls), quote=True)
        stub.write_text(_STUB.format(url=url), encoding="utf-8")
