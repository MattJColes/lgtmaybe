"""Contracts of the in-memory fakes themselves.

``FakeProvider`` grew a schema router because the change overview makes three
concurrent calls with three different ``response_format`` schemas: a single
canned result would serve a diagram payload to the describe call, whose lenient
``wanted`` predicate accepts it — a test passing for the wrong reason.
"""

from __future__ import annotations

import json

from lgtmaybe.core.models import DescribeResult, DiagramResult, ProviderResult
from tests.fakes import FakeProvider

_DESCRIBE = ProviderResult(text='{"title": "described"}', input_tokens=1, output_tokens=1)
_DIAGRAM = ProviderResult(text='{"title": "diagrammed"}', input_tokens=1, output_tokens=1)


def _routed() -> FakeProvider:
    return FakeProvider(
        results_by_schema={DescribeResult: _DESCRIBE, DiagramResult: _DIAGRAM},
    )


def test_results_route_by_response_format_schema() -> None:
    provider = _routed()

    described = provider.complete([], "m", response_format=DescribeResult)
    diagrammed = provider.complete([], "m", response_format=DiagramResult)

    assert described.text == _DESCRIBE.text
    assert diagrammed.text == _DIAGRAM.text


def test_an_unrouted_schema_falls_back_to_the_canned_findings() -> None:
    """FakeEngine calls ``complete`` with no ``response_format`` at all, so a
    router that failed to fall through would break every review test."""
    provider = _routed()

    plain = provider.complete([], "m")
    unknown = provider.complete([], "m", response_format=ProviderResult)

    for result in (plain, unknown):
        assert json.loads(result.text)[0]["title"] == "canned finding"


def test_every_call_is_still_recorded() -> None:
    provider = _routed()

    provider.complete([{"role": "user", "content": "hi"}], "m", response_format=DiagramResult)

    assert provider.calls[0]["opts"]["response_format"] is DiagramResult
    assert provider.calls[0]["model"] == "m"


def test_the_single_result_constructor_still_wins_for_every_call() -> None:
    provider = FakeProvider(result=_DIAGRAM)

    assert provider.complete([], "m", response_format=DescribeResult).text == _DIAGRAM.text
