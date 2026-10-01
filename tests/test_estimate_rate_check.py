"""The desk prices on fal's billing day and trusts a server estimate that adds up (L-20261001-7, fictora-drama #534).

fal reads its dated rates on San Francisco time: the H3 Turbo promo ended at
2026-10-01T07:00Z (12:30 IST). Kit #68 re-priced the server's estimate on the
desk's own day (IST) and took the higher number, so on the morning of 1 Oct
in India it over-quoted $1.20 for a $0.60 film and warned that the server
was stale. The server was right.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from creation import prices
from creation.orchestrate import price_estimate, table_estimate_usd
from creation.production_state import ProductionState

CFG = SimpleNamespace(clip_duration_seconds=15, fallback_estimate_usd=None)
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def _state() -> ProductionState:
    return ProductionState(
        session_id="s", prompt="p", preset_id="pre", preset_version="1"
    )


def _server_answer(
    *, priced_on: str, rate: str, total: str, video: str | None = None
) -> dict:
    """Two 15 s Turbo takes, as ``batches/estimate`` sends them."""

    return {
        "cost_estimate": {
            "priced_on": priced_on,
            "video_endpoint_id": prices.H3_MAX_TURBO_I2V_ENDPOINT,
            "video_resolution": "768P",
            "takes": 2,
            "billed_seconds": 30,
            "usd_per_second": rate,
            "video_usd": video or total,
            "plates": 0,
            "boards": 0,
            "still_usd_each": "0.30",
            "stills_usd": "0",
            "total_usd": total,
        }
    }


@pytest.fixture
def at(monkeypatch: pytest.MonkeyPatch):
    """Pin the kit's clock to one instant."""

    def pin(moment: datetime) -> None:
        monkeypatch.setattr(prices, "now_utc", lambda: moment.astimezone(UTC))

    return pin


@pytest.mark.parametrize(
    ("moment", "day"),
    [
        (datetime(2026, 10, 1, 0, 30, tzinfo=IST), date(2026, 9, 30)),
        (datetime(2026, 10, 1, 10, 0, tzinfo=IST), date(2026, 9, 30)),
        (datetime(2026, 10, 1, 12, 29, 59, tzinfo=IST), date(2026, 9, 30)),
        (datetime(2026, 10, 1, 6, 59, 59, tzinfo=UTC), date(2026, 9, 30)),
        (datetime(2026, 10, 1, 7, 0, tzinfo=UTC), date(2026, 10, 1)),
        (datetime(2026, 10, 1, 13, 0, tzinfo=IST), date(2026, 10, 1)),
    ],
)
def test_the_billing_day_is_fals_not_the_desks(moment: datetime, day: date) -> None:
    assert prices.fal_billing_day(moment) == day


def test_a_naive_time_is_refused_not_guessed() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        prices.fal_billing_day(datetime(2026, 10, 1, 9, 0))


def test_ist_morning_on_the_boundary_takes_the_servers_promo_estimate_without_a_warning(
    at,
) -> None:
    at(datetime(2026, 10, 1, 10, 0, tzinfo=IST))
    estimate = _server_answer(priced_on="2026-09-30", rate="0.02", total="0.60")

    usd, source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2
    )

    assert usd == pytest.approx(0.60)
    assert warnings == []
    assert "server estimate priced 2026-09-30" in source and "$0.02/s" in source


def test_ist_morning_on_the_boundary_the_kits_own_price_is_the_promo_too(at) -> None:
    at(datetime(2026, 10, 1, 10, 0, tzinfo=IST))

    assert table_estimate_usd(_state(), CFG, cast_count=2, takes=2) == pytest.approx(
        0.60
    )


def test_after_1230_ist_the_kit_prices_the_regular_rate(at) -> None:
    at(datetime(2026, 10, 1, 13, 0, tzinfo=IST))

    assert table_estimate_usd(_state(), CFG, cast_count=2, takes=2) == pytest.approx(
        1.20
    )


def test_an_older_server_on_its_utc_day_is_still_taken_as_it_is(at) -> None:
    """A server without #534 prices 02:00Z on 1 Oct as 1 Oct: higher than fal, but its own numbers add up."""

    at(datetime(2026, 10, 1, 2, 0, tzinfo=UTC))
    estimate = _server_answer(priced_on="2026-10-01", rate="0.04", total="1.20")

    usd, source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2
    )

    assert usd == pytest.approx(1.20)
    assert warnings == []
    assert "$0.04/s" in source


def test_a_server_total_that_does_not_add_up_is_flagged_and_the_higher_shown(
    at,
) -> None:
    at(datetime(2026, 10, 1, 13, 0, tzinfo=IST))
    # 30 s x $0.04/s is $1.20, but the server says $0.60.
    estimate = _server_answer(priced_on="2026-10-01", rate="0.04", total="0.60")

    usd, _source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2
    )

    assert usd == pytest.approx(1.20)
    assert warnings and warnings[0].startswith("!! SERVER ESTIMATE DOES NOT ADD UP")
    assert "$0.60" in warnings[0] and "$1.20" in warnings[0]


def test_a_total_that_is_not_video_plus_stills_is_flagged(at) -> None:
    at(datetime(2026, 10, 1, 13, 0, tzinfo=IST))
    estimate = _server_answer(priced_on="2026-10-01", rate="0.04", total="1.20")
    estimate["cost_estimate"].update(plates=1, stills_usd="0.30")

    usd, _source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2
    )

    assert usd == pytest.approx(1.50)
    assert warnings and "$1.20" in warnings[0] and "$1.50" in warnings[0]


def test_a_server_total_with_a_plate_that_adds_up_passes(at) -> None:
    at(datetime(2026, 10, 1, 10, 0, tzinfo=IST))
    estimate = _server_answer(
        priced_on="2026-09-30", rate="0.02", total="0.90", video="0.60"
    )
    estimate["cost_estimate"].update(plates=1, stills_usd="0.30")

    usd, _source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=2, takes=2
    )

    assert usd == pytest.approx(0.90)
    assert warnings == []


def test_r2v_reference_images_on_top_of_the_seconds_are_not_a_mismatch(at) -> None:
    at(datetime(2026, 10, 1, 13, 0, tzinfo=IST))
    estimate = _server_answer(
        priced_on="2026-10-01", rate="0.08", total="2.64", video="2.64"
    )
    estimate["cost_estimate"].update(
        video_endpoint_id=prices.H3_MAX_R2V_ENDPOINT
    )  # 30 s x $0.08 = $2.40, plus reference images

    usd, _source, warnings = price_estimate(
        _state(), CFG, estimate, cast_count=6, takes=2
    )

    assert usd == pytest.approx(2.64)
    assert warnings == []
