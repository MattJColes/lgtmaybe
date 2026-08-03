"""The CLI's graceful wind-down on SIGTERM/SIGINT.

A GitHub Action job that blows its `timeout-minutes`, or a run cancelled by
`cancel-in-progress`, gets a termination signal before it is killed. The CLI
turns the first one into the engine's existing partial-results wind-down;
everything about *installing* that handler is tested here.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from click.testing import CliRunner

from lgtmaybe.cli import graceful_interrupt, main
from lgtmaybe.engine import clear_interrupt, interrupt_requested

_SIGNALS = [getattr(signal, name) for name in ("SIGINT", "SIGTERM") if hasattr(signal, name)]


@pytest.fixture(autouse=True)
def _clean_flag() -> Iterator[None]:
    clear_interrupt()
    yield
    clear_interrupt()


class TestGracefulInterrupt:
    @pytest.mark.parametrize("sig", _SIGNALS)
    def test_installs_and_restores_the_previous_handler(self, sig: signal.Signals) -> None:
        before = signal.getsignal(sig)
        with graceful_interrupt():
            assert signal.getsignal(sig) is not before, "handler not installed"
        assert signal.getsignal(sig) is before, "previous handler not restored"

    @pytest.mark.parametrize("sig", _SIGNALS)
    def test_first_signal_requests_the_wind_down(self, sig: signal.Signals) -> None:
        before = signal.getsignal(sig)
        with graceful_interrupt():
            handler = signal.getsignal(sig)
            assert callable(handler)
            handler(sig, None)  # what the interpreter does on delivery
            assert interrupt_requested(), "the engine was not asked to wind down"
            # A SECOND signal must still kill the process: we only ever
            # intercept one, so the previous handler is back immediately.
            assert signal.getsignal(sig) is before

    def test_off_the_main_thread_is_a_no_op(self) -> None:
        """signal.signal() raises off the main thread — degrade, never crash."""
        errors: list[BaseException] = []

        def run() -> None:
            try:
                with graceful_interrupt():
                    pass
            except BaseException as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=10)
        # A hang leaves `errors` empty too — assert the thread actually finished,
        # or a wedged wind-down would read as success.
        assert not thread.is_alive(), "graceful_interrupt hung off the main thread"
        assert not errors, f"raised off the main thread: {errors}"

    @pytest.mark.skipif(
        not (hasattr(signal, "SIGINT") and hasattr(signal, "SIGTERM")),
        reason="needs both signals to install one and fail the other",
    )
    def test_a_partial_install_restores_what_it_managed_to_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SIGINT installs, then SIGTERM refuses: the SIGINT handler installed a
        moment earlier must not be left behind for the rest of the process."""
        real_signal = signal.signal
        attempts: list[int] = []

        def fake_signal(sig: Any, handler: Any) -> Any:
            attempts.append(sig)
            if len(attempts) == 2:  # the SIGTERM install
                raise ValueError("signal only works in main thread")
            return real_signal(sig, handler)

        monkeypatch.setattr(signal, "signal", fake_signal)
        before = signal.getsignal(signal.SIGINT)
        with graceful_interrupt():
            # A partial install is no install: the previous handler is already
            # back, so the process keeps its normal termination behaviour.
            assert signal.getsignal(signal.SIGINT) is before
        assert attempts == [signal.SIGINT, signal.SIGTERM, signal.SIGINT], (
            f"expected install SIGINT, fail on SIGTERM, then restore SIGINT — got {attempts}"
        )

    def test_main_wraps_the_command_in_the_wind_down(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Installed by the CLI entrypoint, and held open for the subcommand."""
        events: list[str] = []

        @contextmanager
        def _recording() -> Iterator[None]:
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

        def _command_ran() -> str:
            events.append("command")
            return "/tmp/lgtmaybe.yml"

        monkeypatch.setattr("lgtmaybe.cli.graceful_interrupt", _recording)
        monkeypatch.setattr("lgtmaybe.config.store.user_config_path", _command_ran)
        result = CliRunner().invoke(main, ["config", "path"])
        assert result.exit_code == 0, result.output
        assert events == ["enter", "command", "exit"]


def test_importing_the_library_installs_no_handler() -> None:
    """Importing lgtmaybe must never hijack a host application's signals."""
    check = (
        "import signal, sys;"
        "before = signal.getsignal(signal.SIGINT);"
        "import lgtmaybe, lgtmaybe.cli, lgtmaybe.engine;"
        "sys.exit(0 if signal.getsignal(signal.SIGINT) is before else 1)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", check], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"import-time signal handler installed\nstderr: {proc.stderr}"
