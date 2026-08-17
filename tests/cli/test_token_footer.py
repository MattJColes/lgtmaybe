"""A local review always reports what it spent.

The meter used to live behind ``--profile``, so the common case — run the CLI
all day against a hosted provider — gave no running indication of cost at all.
The footer goes to stderr so machine-readable formats stay pipeable.
"""

from __future__ import annotations

import json
from pathlib import Path

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

    def run(fmt: str = "human", profile: bool = False, profile_json=None) -> None:  # type: ignore[no-untyped-def]
        cli.execute_local_review(
            ReviewConfig(provider=Provider.openai, model="m"),
            cli.RuntimeOptions(profile=profile, profile_json=profile_json),
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


class TestProfileDoesNotBreakMachineOutput:
    """`--json --profile` was unpipeable: the findings array and the human table
    went to the same stream, so `lgtmaybe review --json --profile | jq` failed.

    The convention already existed — the token footer is routed to stderr with
    the comment "stderr keeps --json / --agent output pipeable" — and the profile
    table was the one output ignoring it.
    """

    def test_json_output_stays_parseable_under_profile(self, _local_run, capsys) -> None:  # type: ignore[no-untyped-def]
        _local_run(profile=True, fmt="json")
        captured = capsys.readouterr()

        json.loads(captured.out)
        assert "lgtmaybe profile" in captured.err

    def test_agent_output_stays_clean_under_profile(self, _local_run, capsys) -> None:  # type: ignore[no-untyped-def]
        _local_run(profile=True, fmt="agent")
        captured = capsys.readouterr()

        assert "lgtmaybe profile" not in captured.out
        assert "lgtmaybe profile" in captured.err

    def test_a_human_run_keeps_the_table_on_stdout(self, _local_run, capsys) -> None:  # type: ignore[no-untyped-def]
        """Nothing changes for a human reader: stdout is not a machine channel
        there, so moving it would be churn for its own sake."""
        _local_run(profile=True)

        assert "lgtmaybe profile" in capsys.readouterr().out


class TestProfileJsonFile:
    """A file rather than a stream. stdout already carries the findings under
    --json/--agent, and stderr carries the structured logs — a path collides with
    neither, whatever the output format is."""

    def test_it_writes_the_structured_profile(self, _local_run, tmp_path) -> None:  # type: ignore[no-untyped-def]
        target = tmp_path / "profile.json"

        _local_run(profile_json=target)

        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["schema_version"] >= 1
        assert payload["calls"], "the run made calls; they belong in the payload"

    def test_it_coexists_with_json_findings_on_stdout(self, _local_run, tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
        target = tmp_path / "profile.json"

        _local_run(fmt="json", profile_json=target)

        json.loads(capsys.readouterr().out)
        assert json.loads(target.read_text(encoding="utf-8"))["calls"]

    def test_an_unwritable_path_does_not_fail_the_review(
        self, _local_run, tmp_path, capsys
    ) -> None:  # type: ignore[no-untyped-def]
        """A review that produced findings must not be lost to a diagnostic file."""
        _local_run(profile_json=tmp_path / "nope" / "profile.json")

        assert capsys.readouterr().out, "the findings still printed"

    def test_nothing_is_written_when_not_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exercise the early return, not an unrelated directory.

        An empty `tmp_path` proves nothing here: a run without ``--profile-json``
        was never pointed at it, so the assertion held whether or not the default
        path wrote a file somewhere else. Watch the write itself instead.
        """
        written: list[Path] = []
        monkeypatch.setattr(Path, "write_text", lambda self, *_a, **_k: written.append(self))

        cli._write_profile_json(cli.RuntimeOptions())

        assert written == []
