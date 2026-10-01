"""A board redrawn on a finished desk gets its yes on the server too (Hanakaze ep1, option C live run).

`fictora-ops approve --gate board` recorded the yes on the desk only, `fictora-produce approve`
refused ("expected wait_board, got complete"), and film was refused 422
boards_not_approved_for_generation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import set_phase

from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_state import load_production
from fake_api import FakeApi, png_bytes

APPROVE = "/v1/spines/sp1/episodes/1/boards/approve"


def _redrawn_board(desk: Path, api: FakeApi) -> Path:
    board = desk / "ep01" / "boards" / "board-ep01-t1-v2.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_bytes(png_bytes())
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 30.0}]
    }
    api.routes[("POST", APPROVE)] = {"ok": True}
    return board


def _keys(api: FakeApi) -> list[str | None]:
    return [k for m, p, _, k in api.calls if (m, p) == ("POST", APPROVE)]


def test_a_redrawn_board_on_a_finished_desk_is_approved_on_the_server_and_the_phase_kept(
    desk: Path, api: FakeApi
) -> None:
    board = _redrawn_board(desk, api)
    set_phase(desk, "complete", board_paths={"t1": "ep01/boards/board-ep01-t1-v1.png"})

    result = orchestrate.approve_gate(desk, gate="board")

    assert api.posted(APPROVE) == [
        {"spine_version": "v5", "episode_ordinal": 1, "accept_dim": False}
    ]
    (key,) = _keys(api)
    assert key and "reapprove" in key, (
        "a fresh key: the first approval's key would replay the old answer"
    )
    take = episode_by_ordinal(load_series(desk), 1).takes[0]
    assert take.board.status == "approved" and board.name in str(take.board.path)
    assert load_production(desk).phase == "complete"
    assert "approved again on the server" in result.message


def test_two_reapprovals_use_two_keys(desk: Path, api: FakeApi) -> None:
    _redrawn_board(desk, api)
    set_phase(desk, "complete")

    orchestrate.approve_gate(desk, gate="board")
    orchestrate.approve_gate(desk, gate="board", again=True)

    first, second = _keys(api)
    assert first != second


def test_a_board_not_drawn_yet_is_refused(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_script")

    with pytest.raises(RuntimeError, match="no board drawn for episode 1 yet"):
        orchestrate.approve_gate(desk, gate="board")
    assert api.posted(APPROVE) == []


def test_the_cli_takes_again_for_a_board(desk: Path, api: FakeApi) -> None:
    _redrawn_board(desk, api)
    set_phase(desk, "ready_video")

    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "board", "--again"])
        == 0
    )
    assert len(api.posted(APPROVE)) == 1
