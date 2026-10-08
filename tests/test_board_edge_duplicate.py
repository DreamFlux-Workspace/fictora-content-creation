"""The board gate prints the server's same-person-at-both-edges warning, with the free edit (L-20261006-9).

Sweet Racket, 6 Oct 2026: the video model drew a character twice, facing
himself from opposite edges; the take facts said so only after the paid take.
The server now returns the risk on the board exposure route (fictora-drama
``board_edge_risks``) and the kit prints it at the board gate. Warnings only.
"""

from __future__ import annotations

from pathlib import Path

from conftest import SHOWN_PRICES, set_phase
from creation import orchestrate
from creation.spine_view import edge_duplicate_risk_lines
from fake_api import FakeApi
from test_episode_flow_step import _add_episode_two

RISK = {
    "code": "edge_duplicate_risk",
    "severity": "warn",
    "shot_index": 1,
    "frame_ordinals": [1, 2],
    "who": "Dev",
    "cast_id": "cast_02",
    "first_side": "right",
    "later_side": "left",
    "moves": False,
    "fix_frame_ordinal": 2,
    "fix_field": "subject_blocking.1.frame_position",
    "fix_value": "middle plane at frame right",
    "message": "Shot 1 (frames 1-2): Dev is at the right edge in one cell and the left edge in another.",
    "how_to_fix": "Keep Dev on the right in every cell of the shot, then redraw the board before filming.",
}


def _exposure(*risks: dict) -> dict:
    return {
        "boards": [
            {"set_index": 1, "mean_percent": 30.0, "edge_duplicate_risks": list(risks)},
            {"set_index": 2, "mean_percent": 30.0},
        ]
    }


def test_a_risk_prints_the_message_the_fix_and_the_exact_edit() -> None:
    lines = edge_duplicate_risk_lines(_exposure(RISK), desk="desks/sr", episode=3)

    assert lines == [
        f"  !! t1 board: {RISK['message']} (warning only)",
        f"     {RISK['how_to_fix']}",
        "     fictora-produce edit --desk desks/sr --episode 3 --frame 2 "
        "--set 'subject_blocking.1.frame_position=\"middle plane at frame right\"'   "
        "then fictora-produce redraw-board --desk desks/sr --episode 3 --take t1 --cause 'one side per shot'",
    ]


def test_a_risk_with_no_single_field_prints_no_command() -> None:
    risk = {**RISK, "fix_frame_ordinal": None, "fix_field": None, "fix_value": None}

    lines = edge_duplicate_risk_lines(_exposure(risk), desk="d", episode=1)

    assert len(lines) == 2 and "edit --desk" not in "\n".join(lines)


def test_a_quote_in_the_value_stays_one_shell_word() -> None:
    risk = {**RISK, "fix_value": "by Mara's door, frame right"}

    (command,) = [
        line
        for line in edge_duplicate_risk_lines(_exposure(risk), desk="d", episode=1)
        if "edit" in line
    ]

    assert "by Mara'\\''s door" in command


def test_only_the_boards_just_drawn_and_nothing_from_an_older_server() -> None:
    assert (
        edge_duplicate_risk_lines(_exposure(RISK), desk="d", episode=1, sets=[2]) == []
    )
    assert (
        edge_duplicate_risk_lines(
            {"boards": [{"set_index": 1, "mean_percent": 30.0}]}, desk="d", episode=1
        )
        == []
    )
    assert edge_duplicate_risk_lines(None, desk="d", episode=1) == []


def test_the_board_gate_prints_it_after_the_shot_list(desk: Path, api: FakeApi) -> None:
    _add_episode_two(desk, api)
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/2/boards/exposure")] = {
        "boards": [
            {"set_index": 1, "mean_percent": 30.0, "edge_duplicate_risks": [RISK]}
        ]
    }

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert f"!! t1 board: {RISK['message']} (warning only)" in result.message
    assert "--frame 2 --set 'subject_blocking.1.frame_position=" in result.message
    assert result.message.index("row 1:") < result.message.index("!! t1 board: Shot 1")
