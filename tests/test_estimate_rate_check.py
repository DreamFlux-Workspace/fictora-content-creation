"""A server estimate priced at another day's rate is caught, shown, and never the lower number (L-20261001-7)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from creation import prices
from creation.orchestrate import price_estimate
from creation.production_state import ProductionState

CFG = SimpleNamespace(clip_duration_seconds=15, fallback_estimate_usd=None)


def _state() -> ProductionState:
    return ProductionState(
        session_id="s", prompt="p", preset_id="pre", preset_version="1"
    )


def _server_answer(*, priced_on: str, rate: str, total: str) -> dict:
    """What the server sent on 1 Oct: two 15 s Turbo takes priced on its own (UTC) day."""

    return {
        "cost_estimate": {
            "priced_on": priced_on,
            "video_endpoint_id": prices.H3_MAX_TURBO_I2V_ENDPOINT,
            "video_resolution": "768P",
            "takes": 2,
            "billed_seconds": 30,
            "usd_per_second": rate,
            "video_usd": total,
            "plates": 0,
            "boards": 0,
            "still_usd_each": "0.30",
            "stills_usd": "0",
            "total_usd": total,
        }
    }


def test_a_server_total_at_yesterdays_promo_rate_is_repriced_at_todays_rate_and_flagged() -> (
    None
):
    estimate = _server_answer(priced_on="2026-09-30", rate="0.02", total="0.60")

    usd, source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2, on=date(2026, 10, 1)
    )

    assert usd == pytest.approx(1.20)  # 2 takes x 15 s x $0.04/s
    assert warnings and warnings[0].startswith("!! SERVER ESTIMATE DISAGREES")
    assert "$0.60" in warnings[0] and "$1.20" in warnings[0]
    assert "2026-09-30" in warnings[0] and "$0.02/s" in warnings[0]
    assert "$0.04/s" in source


def test_a_stale_server_total_with_a_plate_keeps_the_plate_and_reprices_the_seconds() -> (
    None
):
    estimate = _server_answer(priced_on="2026-09-30", rate="0.02", total="0.90")
    estimate["cost_estimate"].update(plates=1, stills_usd="0.30")

    usd, _source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2, on=date(2026, 10, 1)
    )

    assert usd == pytest.approx(1.50)
    assert "$0.90" in warnings[0] and "$1.50" in warnings[0]


def test_a_server_total_at_todays_rate_passes_without_a_warning() -> None:
    estimate = _server_answer(priced_on="2026-10-01", rate="0.04", total="1.20")

    usd, source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2, on=date(2026, 10, 1)
    )

    assert usd == pytest.approx(1.20)
    assert warnings == []
    assert "server estimate priced 2026-10-01" in source


def test_a_server_rate_above_the_kits_is_never_priced_down() -> None:
    estimate = _server_answer(priced_on="2026-10-01", rate="0.04", total="1.20")

    usd, _source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2, on=date(2026, 9, 30)
    )

    assert usd == pytest.approx(1.20)  # the higher of the two
    assert warnings and "$0.60" in warnings[0]
