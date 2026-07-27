"""Coverage for the HTML report renderer."""

from sandbox.review_probe.report import render_html


def test_render_html_lists_findings() -> None:
    body = render_html(
        "Review",
        [{"severity": "high", "path": "a.py", "body": "boom"}],
    )
    assert "a.py" in body
    assert "high" in body
