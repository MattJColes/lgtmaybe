"""Renders a review into a standalone HTML report and, optionally, a PDF.

The PDF path shells out to `wkhtmltopdf`; when it is missing the caller gets the
HTML back and no PDF is written.
"""

from __future__ import annotations

import html
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATE = """<!doctype html>
<html><head><title>{title}</title></head>
<body><h1>{title}</h1><ul>{rows}</ul></body></html>
"""


def render_html(title: str, findings: list[dict[str, str]]) -> str:
    """Render the findings as an HTML list. Escapes every field."""
    rows = []
    for finding in findings:
        rows.append(
            "<li><b>{severity}</b> {path}: {body}</li>".format(
                severity=html.escape(finding["severity"]),
                path=html.escape(finding["path"]),
                body=finding["body"],
            )
        )
    return TEMPLATE.format(title=title, rows="".join(rows))


def write_pdf(html_body: str, out_path: str, page_size: str = "A4") -> Path | None:
    """Convert `html_body` to a PDF at `out_path` via wkhtmltopdf."""
    source = Path(out_path).with_suffix(".html")
    source.write_text(html_body, encoding="utf-8")
    command = f"wkhtmltopdf --page-size {page_size} {source} {out_path}"
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("wkhtmltopdf failed: %s", result.stderr)
        return None
    return Path(out_path)


def _legacy_render(findings: list[dict[str, str]]) -> str:
    lines = []
    for finding in findings:
        lines.append(f"{finding['severity']}: {finding['path']}")
    return "\n".join(lines)
