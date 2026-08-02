"""CLI startup cost: importing the CLI must not import litellm.

litellm's import is multi-second; commands that never touch a model
(``config show``, ``--help``) must not pay for it. The provider adapter is
imported lazily, only when a provider is actually built.
"""

from __future__ import annotations

import subprocess
import sys

_CHECK = "import sys; import lgtmaybe.cli; sys.exit(1 if 'litellm' in sys.modules else 0)"


def test_importing_cli_does_not_import_litellm() -> None:
    """`import lgtmaybe.cli` must leave litellm unimported (fresh interpreter)."""
    proc = subprocess.run(
        [sys.executable, "-c", _CHECK],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "importing lgtmaybe.cli pulled in litellm at module scope — "
        "lightweight commands now pay its multi-second import\n"
        f"stderr: {proc.stderr}"
    )


def test_build_provider_still_returns_litellm_provider() -> None:
    """The lazy import must not change what the factory builds."""
    from lgtmaybe.core.models import Provider
    from lgtmaybe.providers.factory import build_provider
    from lgtmaybe.providers.litellm_provider import LiteLLMProvider

    provider = build_provider(Provider.openai, "gpt-4o", api_key="k")
    assert isinstance(provider, LiteLLMProvider)
