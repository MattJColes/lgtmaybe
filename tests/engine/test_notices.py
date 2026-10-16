"""The summary's final shape, composed on its own (see ``notices._compose_summary``)."""

from __future__ import annotations

from lgtmaybe.core.models import RiskAssessment
from lgtmaybe.engine.lenses import _Lens
from lgtmaybe.engine.notices import LENSES_MARKER_PREFIX, _compose_summary
from lgtmaybe.engine.risk import risk_marker

_LENSES = [_Lens(id="security", user_block=""), _Lens(id="correctness", user_block="")]
_MARKER = f"{LENSES_MARKER_PREFIX}correctness,security -->"


def _compose(notices: list[str], *, clean: bool, **kwargs: object) -> str:
    options: dict[str, object] = {"lenses": _LENSES, "failed_lenses": [], "risk": None}
    options.update(kwargs)
    return _compose_summary(notices, "0 findings · line", clean=clean, **options)  # type: ignore[arg-type]


def test_a_clean_review_without_notices_says_lgtm() -> None:
    assert _compose([], clean=True) == f"👍 LGTM!\n\n0 findings · line\n{_MARKER}"


def test_findings_without_notices_are_just_the_line() -> None:
    assert _compose([], clean=False) == f"0 findings · line\n{_MARKER}"


def test_a_notice_takes_the_lgtm_away() -> None:
    summary = _compose(["⚠️ one", "🔧 two"], clean=True)
    assert summary == f"⚠️ one\n\n🔧 two\n\n0 findings · line\n{_MARKER}"


def test_a_failed_lens_is_left_out_of_the_marker() -> None:
    summary = _compose([], clean=False, failed_lenses=["security"])
    assert summary.endswith(f"{LENSES_MARKER_PREFIX}correctness -->")


def test_the_risk_marker_rides_above_the_lenses_marker() -> None:
    risk = RiskAssessment(level="high")
    marker = risk_marker(risk)
    assert marker
    assert _compose([], clean=False, risk=risk).endswith(f"{marker}\n{_MARKER}")
