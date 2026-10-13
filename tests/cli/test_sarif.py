"""`--format sarif` renders findings as a SARIF 2.1.0 log for code-scanning upload.

The renderer is pure and deterministic: the same findings always produce the same
bytes, so a re-run uploads an identical log and the consumer's own matching (which
keys on ``partialFingerprints``) recognises the findings it already holds.
"""

from __future__ import annotations

import json
from typing import Any

from lgtmaybe.cli import render_findings
from lgtmaybe.cli.render import SARIF_LEVELS, render_sarif
from lgtmaybe.core.findings import finding_fingerprint, finding_identity
from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.core.version import package_version

_FINDING = ReviewFinding(
    path="src/app.py",
    line=42,
    severity=Severity.high,
    title="possible NPE",
    body="`user` may be None here.",
    suggestion="if user is not None:",
    anchor="return user.name",
    confidence=8,
    category="correctness",
)


def _finding(**overrides: Any) -> ReviewFinding:
    return _FINDING.model_copy(update=overrides)


def _log(findings: list[ReviewFinding], *, complete: bool = True) -> dict[str, Any]:
    return json.loads(render_sarif(findings, complete=complete))


def _run(findings: list[ReviewFinding]) -> dict[str, Any]:
    log = _log(findings)
    assert len(log["runs"]) == 1
    return log["runs"][0]


def test_log_declares_sarif_2_1_0_and_the_lgtmaybe_driver() -> None:
    log = _log([_FINDING])

    assert log["version"] == "2.1.0"
    assert "sarif" in log["$schema"] and "2.1.0" in log["$schema"]
    driver = log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "lgtmaybe"
    assert driver["version"] == package_version()
    assert driver["informationUri"] == "https://lgtmaybe.coles.codes/"


def test_result_carries_rule_message_location_and_properties() -> None:
    (result,) = _run([_FINDING])["results"]

    assert result["ruleId"] == "correctness"
    assert result["level"] == "error"
    assert "possible NPE" in result["message"]["text"]
    assert "`user` may be None here." in result["message"]["text"]
    (location,) = result["locations"]
    physical = location["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "src/app.py"
    assert physical["region"] == {"startLine": 42}
    assert result["properties"]["severity"] == "high"
    assert result["properties"]["confidence"] == 8
    # Free text cannot be expressed as a SARIF `fixes` replacement without a
    # region it replaces, so it travels in the property bag instead.
    assert result["properties"]["suggestion"] == "if user is not None:"
    assert "fixes" not in result


def test_rule_index_points_at_the_results_rule() -> None:
    run = _run([_FINDING, _finding(category="security", title="SQL injection")])

    rules = run["tool"]["driver"]["rules"]
    for result in run["results"]:
        assert rules[result["ruleIndex"]]["id"] == result["ruleId"]


def test_every_severity_maps_to_a_sarif_level() -> None:
    expected = {
        Severity.critical: "error",
        Severity.high: "error",
        Severity.medium: "warning",
        Severity.low: "note",
        Severity.info: "note",
    }
    # Derived from the enum, so a new Severity member fails here until it is mapped.
    assert set(SARIF_LEVELS) == set(Severity)
    assert set(expected) == set(Severity)
    for severity in Severity:
        (result,) = _run([_finding(severity=severity)])["results"]
        assert result["level"] == expected[severity]


def test_partial_fingerprints_reuse_the_posting_ids() -> None:
    (result,) = _run([_FINDING])["results"]

    fingerprints = result["partialFingerprints"]
    assert fingerprints["lgtmaybeFingerprint/v1"] == finding_fingerprint(
        _FINDING.path, _FINDING.title
    )
    assert fingerprints["lgtmaybeIdentity/v1"] == finding_identity(_FINDING)


def test_rules_are_deduped_in_a_stable_order() -> None:
    findings = [
        _finding(category="security", title="a"),
        _finding(category="correctness", title="b"),
        _finding(category="security", title="c"),
    ]

    forward = _run(findings)["tool"]["driver"]["rules"]
    backward = _run(list(reversed(findings)))["tool"]["driver"]["rules"]

    assert [rule["id"] for rule in forward] == ["correctness", "security"]
    assert forward == backward


def test_uncategorised_finding_gets_a_fallback_rule() -> None:
    run = _run([_finding(category=None)])

    (result,) = run["results"]
    assert result["ruleId"] == "review"
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == ["review"]


def test_unanchored_finding_has_no_region() -> None:
    """An anchor that matched nothing leaves `line` a guess — never pin it."""
    (result,) = _run([_finding(anchored=False)])["results"]

    physical = result["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "src/app.py"
    assert "region" not in physical
    assert result["properties"]["anchored"] is False


def test_left_side_finding_has_no_region() -> None:
    """A LEFT-side line numbers the old file, which the consumer's checkout lacks."""
    (result,) = _run([_finding(side="LEFT")])["results"]

    assert "region" not in result["locations"][0]["physicalLocation"]


def test_uri_is_forward_slashed_and_percent_encoded() -> None:
    (result,) = _run([_finding(path="docs\\my notes/a#b.md")])["results"]

    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "docs/my%20notes/a%23b.md"


def test_empty_findings_still_produce_a_valid_run() -> None:
    log = _log([])

    (run,) = log["runs"]
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []
    assert run["invocations"] == [{"executionSuccessful": True}]


def test_incomplete_review_is_flagged_on_the_invocation() -> None:
    (invocation,) = _log([_FINDING], complete=False)["runs"][0]["invocations"]

    assert invocation["executionSuccessful"] is False
    (notice,) = invocation["toolExecutionNotifications"]
    assert notice["level"] == "warning"
    assert "incomplete" in notice["message"]["text"]


def test_non_ascii_prose_is_escaped_and_round_trips() -> None:
    """A redirect on a non-UTF-8 console must still write a decodable file."""
    out = render_sarif([_finding(title="café crash", path="src/naïve.py")])

    assert out.isascii()
    (result,) = json.loads(out)["runs"][0]["results"]
    assert result["message"]["text"].startswith("café crash")
    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "src/na%C3%AFve.py"


def test_output_is_deterministic() -> None:
    findings = [_FINDING, _finding(category="security", title="other", line=7)]

    assert render_sarif(findings) == render_sarif(findings)


def test_render_findings_dispatches_sarif_and_reads_completeness() -> None:
    complete = json.loads(render_findings([_FINDING], "1 finding", fmt="sarif"))
    incomplete = json.loads(
        render_findings(
            [_FINDING],
            "lens failed; results may be incomplete.\n<!-- lgtmaybe-incomplete -->",
            fmt="sarif",
        )
    )

    assert complete["runs"][0]["invocations"][0]["executionSuccessful"] is True
    assert incomplete["runs"][0]["invocations"][0]["executionSuccessful"] is False
