"""Verify that docs/llms.txt and docs/llms-full.txt match the committed output.

If this test fails, run `uv run python docs/generate_llms_txt.py` and commit the
updated files.
"""

import sys
from pathlib import Path

# Allow importing the generator without it being a package.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "docs"))

from generate_llms_txt import generate_full, generate_index  # noqa: E402

DOCS = Path(__file__).parent.parent.parent / "docs"


def test_llms_txt_matches_committed_file() -> None:
    committed = (DOCS / "llms.txt").read_text(encoding="utf-8")
    assert generate_index() == committed, (
        "docs/llms.txt is stale. "
        "Run `uv run python docs/generate_llms_txt.py` and commit the result."
    )


def test_llms_full_txt_matches_committed_file() -> None:
    committed = (DOCS / "llms-full.txt").read_text(encoding="utf-8")
    assert generate_full() == committed, (
        "docs/llms-full.txt is stale. "
        "Run `uv run python docs/generate_llms_txt.py` and commit the result."
    )
