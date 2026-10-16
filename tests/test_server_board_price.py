"""Board prices and board spend come from the server (server-heavy rule; Low Tide, 16 Oct 2026).

A shot-clips episode draws ONE grid for both its takes; the kit priced and booked it as $0.30 a take board.
"""

from __future__ import annotations

from pathlib import Path

from conftest import SHOWN_PRICES, set_phase
from creation import orchestrate
from creation.ops.floor import load_series
from creation.picture_spend import (
    distinct_board_pictures,
    server_board_price,
    server_picture_spend,
)
from fake_api import FakeApi

ESTIMATE = "/v1/spines/sp1/batches/estimate"
ENROL = "/v1/spines/sp1/boards/enrol"
EXPOSURE = "/v1/spines/sp1/episodes/1/boards/exposure"
ONE_GRID = {"boards": 1, "plates": 0, "still_usd_each": "0.30", "settled_usd": "0.30"}


def _second_take_board(api: FakeApi, *, same_picture: bool) -> None:
    """Episode 1's take 2 board: the same grid picture as take 1 (shot clips), or its own."""

    first = api.spine_doc["media_assets"][0]
    url = first["url"] if same_picture else first["url"].replace("board", "board-t2")
    api.spine_doc["media_assets"].insert(
        1,
        {**first, "asset_id": first["asset_id"].replace("set01", "set02"), "url": url},
    )


def _draw(api: FakeApi, desk: Path, *, result: dict | None) -> str:
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", ENROL)] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {
        "job_id": "job_boards",
        "status": "completed",
        **({"result": result} if result is not None else {}),
    }
    api.routes[("GET", EXPOSURE)] = {"boards": []}
    return orchestrate.run_step(desk, confirm_spend=True).message


def _board_spend(desk: Path) -> list[tuple[str | None, float]]:
    return [
        (e.take_id, e.usd) for e in load_series(desk).spend_log if e.unit == "board"
    ]


def test_one_grid_for_two_takes_is_booked_once_at_the_servers_price(
    desk: Path, api: FakeApi
) -> None:
    _second_take_board(api, same_picture=True)

    message = _draw(api, desk, result={"picture_spend": ONE_GRID})

    assert _board_spend(desk) == [(None, 0.30)]
    assert "Drew 2 board(s): $0.30 booked (server)." in message
    assert "!! The server did not say" not in message


def test_an_older_server_books_each_distinct_picture_from_the_kit_table(
    desk: Path, api: FakeApi
) -> None:
    _second_take_board(api, same_picture=True)

    message = _draw(api, desk, result=None)

    # The grid's two takes share one picture: booked once, and the human is told it is the kit's count.
    assert _board_spend(desk) == [(None, 0.30)]
    assert (
        "$0.30 booked (kit table)" in message and "!! The server did not say" in message
    )


def test_one_board_a_take_is_still_booked_on_each_take(
    desk: Path, api: FakeApi
) -> None:
    message = _draw(
        api, desk, result={"picture_spend": {**ONE_GRID, "settled_usd": "0.30"}}
    )

    assert _board_spend(desk) == [("t1", 0.30)]
    assert "$0.30 booked (server)" in message


def test_the_board_price_shown_before_the_draw_is_the_servers(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_boards_enrol")
    api.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"boards": 1, "still_usd_each": "0.30"}
    }

    message = orchestrate.run_step(desk, confirm_spend=False).message

    assert (
        "1 storyboard(s) (server estimate), about $0.30. Nothing drawn yet." in message
    )
    assert api.posted(ENROL) == []


def test_a_refused_estimate_prices_from_the_kit_table_and_says_so(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_boards_enrol")
    api.routes[("POST", ESTIMATE)] = (
        422,
        {"error": {"code": "board_behind_script_edit"}},
    )

    message = orchestrate.run_step(desk, confirm_spend=False).message

    assert "!! kit table: the server gave no board price" in message
    assert api.posted(ENROL) == []


def test_the_readers_take_only_what_the_server_says() -> None:
    assert server_picture_spend({"result": {"picture_spend": ONE_GRID}}) == {
        "boards": 1.0,
        "plates": 0.0,
        "still_usd_each": 0.30,
        "settled_usd": 0.30,
    }
    assert server_picture_spend({"result": {}}) is None
    assert server_picture_spend({"result": {"picture_spend": {"boards": "x"}}}) is None
    assert server_picture_spend(None) is None

    class _Run:
        def __init__(self, answer):
            self.answer = answer

        def spine(self, _spine_id):
            return {
                "spine_version": "sha256:" + "a" * 64,
                "episode_summaries": [{"episode_id": "ep01", "ordinal": 1}],
            }

        def post_optional(self, _path, _body):
            return self.answer

    assert server_board_price(
        _Run((200, {"cost_estimate": {"boards": 1, "still_usd_each": "0.30"}})),
        spine_id="s",
        episode=1,
    ) == (1, 0.30)
    assert (
        server_board_price(_Run((422, {"error": {}})), spine_id="s", episode=1) is None
    )
    # An error answer is never read as a price, whatever it carries.
    refused = (500, {"cost_estimate": {"boards": 1, "still_usd_each": "0.30"}})
    assert server_board_price(_Run(refused), spine_id="s", episode=1) is None
    assert (
        server_board_price(
            _Run((200, {"cost_estimate": {"still_usd_each": "0.30"}})),
            spine_id="s",
            episode=1,
        )
        is None
    )
    assert (
        server_board_price(
            _Run((200, {"cost_estimate": {"boards": True, "still_usd_each": "0.30"}})),
            spine_id="s",
            episode=1,
        )
        is None
    )


def test_distinct_pictures_count_a_shared_grid_once(api: FakeApi) -> None:
    _second_take_board(api, same_picture=True)
    assert distinct_board_pictures(api.spine_doc, episode=1, sets=[1, 2]) == 1
    _second_take_board(api, same_picture=False)
    assert distinct_board_pictures(api.spine_doc, episode=1, sets=[1, 2]) == 2
