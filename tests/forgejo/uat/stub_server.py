"""Serve the e2e stub model over the network, for the local Forgejo harness.

The stub (``tests/e2e/stub_provider.py``) normally binds loopback inside a test
process. A Forgejo job container reaches it across the compose network, so this
runs it on a chosen host and port until stopped. Planted ``@flag`` markers in a
pull request's diff decide what it "finds", exactly as in the CLI e2e tests.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

# Run with `python -I`, so the repository root is added explicitly rather than
# trusting the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.e2e.stub_provider import StubServer, start_stub  # noqa: E402


def serve(host: str, port: int) -> StubServer:
    """Start the stub on ``host``:``port`` and return it (non-blocking)."""
    return start_stub(port=port, host=host)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    serve(args.host, args.port)
    print(f"stub model listening on {args.host}:{args.port}", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
