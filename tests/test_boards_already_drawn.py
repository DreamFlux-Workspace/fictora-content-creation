"""`step` on a desk whose boards the server already drew picks them up: no price, no draw, nothing booked.

Low Tide ep 2 (16 Oct 2026): the server held both takes' boards from an earlier run.
"""

from __future__ import annotations

from pathlib import Path

from conftest import SHOWN_PRICES, set_phase
from creation import orchestrate
from creation.ops.floor import load_series
from creation.production_state import load_production
from fake_api import FakeApi

ENROL = "/v1/spines/sp1/boards/enrol"
EXPOSURE = "/v1/spines/sp1/episodes/1/boards/exposure"


def _board_spend(desk: Path) -> list[float]:
    return [entry.usd for entry in load_series(desk).spend_log if entry.unit == "board"]


def test_boards_the_server_already_drew_are_picked_up_with_no_price_and_no_spend(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["media_assets"][0]["source_job_id"] = "job_board_ep01_set01"
    api.routes[("GET", EXPOSURE)] = {"boards": []}
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert result.phase == "wait_board" and load_production(desk).phase == "wait_board"
    assert api.posted(ENROL) == []
    assert _board_spend(desk) == []
    assert (
        "Picked up 1 board(s): $0.00 booked (already drawn on the server; nothing new drawn)."
        in result.message
    )


def test_a_board_the_server_did_not_draw_is_still_priced_first(
    desk: Path, api: FakeApi
) -> None:
    # No drawing job on the board (or a stale one): the step prices the draw and sends nothing.
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert (
        result.phase == "ready_boards_enrol" and "Nothing drawn yet" in result.message
    )
    stale = api.spine_doc["media_assets"][0]
    stale.update(source_job_id="job_old", stale=True)
    assert orchestrate.run_step(desk).phase == "ready_boards_enrol"
    assert api.posted(ENROL) == []


def test_an_enrol_the_server_answers_already_drawn_books_nothing(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ENROL)] = SystemExit("HTTP 409 boards_already_generated")
    api.routes[("GET", EXPOSURE)] = {"boards": []}
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert result.phase == "wait_board"
    assert _board_spend(desk) == []
    assert "$0.00 booked (already drawn on the server" in result.message
