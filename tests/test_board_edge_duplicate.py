"""The board gate prints the server's same-person-at-both-edges warning, calmly (L-20261006-9).

Sweet Racket, 6 Oct 2026: the video model drew a character twice, facing
himself from opposite edges; the take facts said so only after the paid take.
The server keeps a new show's people on one side before it draws and returns
what is left on the board exposure route (fictora-drama ``board_edge_risks``,
only on boards awaiting approval). Founder, 8 Oct 2026: one ``!!`` line per
take with its redraw once; a stated walk across is one quiet information line.
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


def test_one_take_prints_one_line_and_its_redraw_once() -> None:
    mira = {**RISK, "shot_index": 3, "who": "Mira", "cast_id": "cast_03"}
    dev_again = {
        **RISK,
        "fix_frame_ordinal": 3,
    }  # a second picture of shot 1: still one shot

    lines = edge_duplicate_risk_lines(
        _exposure(RISK, dev_again, mira), desk="desks/sr", episode=3
    )

    assert lines == [
        "  !! t1 board: 2 shots may show a character twice (shot 1: Dev; shot 3: Mira). "
        'Fix: redraw t1 with "keep each character on one side within a shot" (warning only)',
        "     fictora-produce redraw-board --desk desks/sr --episode 3 --take t1 "
        '--note "keep each character on one side within a shot"',
    ]


def test_one_shot_reads_in_the_singular() -> None:
    (line, _command) = edge_duplicate_risk_lines(_exposure(RISK), desk="d", episode=1)

    assert line.startswith(
        "  !! t1 board: 1 shot may show a character twice (shot 1: Dev)."
    )


def test_a_stated_walk_is_one_quiet_information_line() -> None:
    walk = {**RISK, "shot_index": 2, "who": "The statue", "moves": True}

    assert edge_duplicate_risk_lines(_exposure(walk), desk="d", episode=1) == [
        "  t1 board: a character walks across the frame (shot 2: The statue); watch that shot in the take. Information only."
    ]
    assert not any(
        "!!" in line
        for line in edge_duplicate_risk_lines(_exposure(walk), desk="d", episode=1)
    )


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

    assert (
        "!! t1 board: 1 shot may show a character twice (shot 1: Dev)."
        in result.message
    )
    assert result.message.count("redraw-board --desk") >= 1
    assert result.message.index("row 1:") < result.message.index("!! t1 board: 1 shot")
