"""A local review always reports what it spent.

The meter used to live behind ``--profile``, so the common case — run the CLI
all day against a hosted provider — gave no running indication of cost at all.
The footer goes to stderr so machine-readable formats stay pipeable.
"""

from __future__ import annotations

import click
import pytest

import lgtmaybe.cli as cli
from lgtmaybe.core.models import PRContext, Provider, ReviewConfig
from lgtmaybe.engine.profiling import profiler

_CTX = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+new line\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="local/local",
    pr_number=0,
)


@pytest.fixture(autouse=True)
def _fresh_profiler() -> None:
    profiler.reset()


class _StubEngine:
    def review(self, ctx, cfg):  # type: ignore[no-untyped-def]
        # Stand in for the real fan-out: record the spend a run would have.
        profiler.record_call(
            label="security",
            batch=1,
            elapsed=1.0,
            attempts=1,
            input_tokens=36_918,
            output_tokens=1_286,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
        return [], "👍 LGTM!"


@pytest.fixture
def _local_run(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Run execute_local_review against stubbed git + provider plumbing."""
    monkeypatch.setattr(cli, "local_file_reader", lambda: None)
    monkeypatch.setattr(cli, "build_symbol_resolver", lambda _: None)
    monkeypatch.setattr(cli, "build_provider_engine", lambda *a, **k: (_StubEngine(), object()))
    monkeypatch.setattr(cli, "local_pr_context", lambda **k: _CTX)

    def run(fmt: str = "human", profile: bool = False) -> None:
        cli.execute_local_review(
            ReviewConfig(provider=Provider.openai, model="m"),
            cli.RuntimeOptions(profile=profile),
            base=None,
            working=False,
            fmt=fmt,
        )

    return run


class TestLocalTokenFooter:
    def test_footer_reports_the_run_s_spend(self, _local_run, capsys) -> None:  # type: ignore[no-untyped-def]
        _local_run()
        err = capsys.readouterr().err
        assert "38,204 billable" in err
        assert "36,918 in" in err and "1,286 out" in err

    def test_footer_goes_to_stderr_so_json_stays_pipeable(self, _local_run, capsys) -> None:  # type: ignore[no-untyped-def]
        _local_run(fmt="json")
        captured = capsys.readouterr()
        assert "billable" not in captured.out, "stdout must stay machine-readable"
        assert "billable" in captured.err

    def test_a_run_that_called_no_model_prints_no_footer(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:  # type: ignore[no-untyped-def]
        """Nothing was spent, so there is nothing to report — a zero line is noise."""

        class _FreeEngine:
            def review(self, ctx, cfg):  # type: ignore[no-untyped-def]
                return [], "👍 LGTM!"

        monkeypatch.setattr(cli, "local_file_reader", lambda: None)
        monkeypatch.setattr(cli, "build_symbol_resolver", lambda _: None)
        monkeypatch.setattr(cli, "build_provider_engine", lambda *a, **k: (_FreeEngine(), object()))
        monkeypatch.setattr(cli, "local_pr_context", lambda **k: _CTX)
        cli.execute_local_review(
            ReviewConfig(provider=Provider.openai, model="m"),
            cli.RuntimeOptions(),
            base=None,
            working=False,
            fmt="human",
        )
        assert "billable" not in capsys.readouterr().err

    def test_profile_does_not_double_report(self, _local_run, capsys) -> None:  # type: ignore[no-untyped-def]
        """--profile already ends with the same total; one is enough."""
        _local_run(profile=True)
        err = capsys.readouterr().err
        assert err.count("billable") == 0, "the profile table carries the total instead"

    def test_a_failed_review_prints_no_footer(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:  # type: ignore[no-untyped-def]
        """The error is the message; a spend line under it is noise."""

        class _BrokenEngine:
            def review(self, ctx, cfg):  # type: ignore[no-untyped-def]
                raise RuntimeError("provider exploded")

        monkeypatch.setattr(cli, "local_file_reader", lambda: None)
        monkeypatch.setattr(cli, "build_symbol_resolver", lambda _: None)
        monkeypatch.setattr(
            cli, "build_provider_engine", lambda *a, **k: (_BrokenEngine(), object())
        )
        monkeypatch.setattr(cli, "local_pr_context", lambda **k: _CTX)
        with pytest.raises(click.ClickException):
            cli.execute_local_review(
                ReviewConfig(provider=Provider.openai, model="m"),
                cli.RuntimeOptions(),
                base=None,
                working=False,
                fmt="human",
            )
        assert "billable" not in capsys.readouterr().err
