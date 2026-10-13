"""`edit --beat N --shot "...|seconds"`: a planned shot's length (fictora-drama shot seconds, on #765).

A slow push-in held 3 s turned a 3-4 s reveal into 6.5 s (L-20261005-9): shot
lengths came only from the tempo. A shot of a beat's plan may now say how long
it plays; the kit checks it like the server does and prints it in the shot list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from creation import shot_plan as sp
from creation.cli_produce import main as produce_main
from creation.spine_view import ShotRow, shot_rows
from fake_api import FakeApi, spine_fixture
from test_edit_shot_plan import _patches, _server_keeps_plans


def test_the_fifth_shot_part_is_its_seconds() -> None:
    plan = sp.plan_from_shots(["close-up|her face|slow push in||3", "wide|the shop|locked|high angle|2.5 s"])

    assert plan == [
        {"size": "close-up", "subject": "her face", "camera": "slow push in", "seconds": 3.0},
        {"size": "wide", "subject": "the shop", "camera": "locked", "angle": "high angle", "seconds": 2.5},
    ]
    # Without the fifth part a shot is exactly what it was.
    assert sp.plan_from_shots(["wide|the shop"]) == [{"size": "wide", "subject": "the shop"}]


def test_json_plans_take_seconds_as_a_number() -> None:
    assert sp.normalize_plan([{"size": "wide", "subject": "x", "seconds": 4}]) == [
        {"size": "wide", "subject": "x", "seconds": 4.0}
    ]


@pytest.mark.parametrize(
    ("seconds", "why"),
    [("0.2", "0.5-15 seconds"), ("20", "0.5-15 seconds"), ("soon", "a number like 3"), (True, "must be a number")],
)
def test_seconds_the_server_would_refuse_stop_before_any_call(seconds: Any, why: str) -> None:
    with pytest.raises(sp.ShotPlanError, match=why):
        sp.normalize_plan([{"size": "wide", "subject": "x", "seconds": seconds}])


def test_the_shot_list_prints_a_planned_shots_seconds() -> None:
    assert sp.describe_shot({"size": "close-up", "subject": "her face", "seconds": 3.0}) == (
        "close-up on her face, plays 3 s"
    )
    assert sp.describe_shot({"size": "close-up", "subject": "her face"}) == "close-up on her face"
    assert sp.plan_lines([{"size": "wide", "subject": "x", "seconds": 2.5}]) == ["    plan shot 1: wide on x, plays 2.5 s"]


def test_a_server_that_drops_the_seconds_is_not_holding_the_plan() -> None:
    sent = [{"size": "wide", "subject": "x", "seconds": 3.0}]
    assert not sp.same_plan([{"size": "wide", "subject": "x"}], sent)
    assert sp.same_plan([{"size": "wide", "subject": "x", "seconds": 3}], sent)


def test_a_writers_timed_row_shows_in_the_shot_list_row() -> None:
    frames = [
        {"board_row": 1, "visual_brief": {"shot_scale": "close-up", "row_direction": {"camera_move": "dolly_in", "seconds": 3}}},
        {"board_row": 1, "visual_brief": {"shot_scale": "close-up"}},
        {"board_row": 2, "visual_brief": {"shot_scale": "wide", "row_direction": {"camera_move": "locked"}}},
    ]
    rows = shot_rows(frames)

    assert rows[0].seconds == 3.0 and rows[0].one_line().endswith("· plays 3 s")
    assert rows[1].seconds is None and "plays" not in rows[1].one_line()
    assert ShotRow(1, "a", "b", "c", "d", (), ()).one_line() == (
        "row 1: a · b · c · camera d · cells ? · reaction none"
    )


def test_edit_shot_sends_the_seconds_and_prints_them(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_plans(api)

    assert produce_main(
        ["edit", "--desk", str(desk), "--episode", "1", "--beat", "1", "--shot", "close-up|her face|slow push in||3"]
    ) == 0

    planned = [{"size": "close-up", "subject": "her face", "camera": "slow push in", "seconds": 3.0}]
    assert _patches(api) == [{"beats": [{"beat_id": "beat_episode_01_01", "shot_plan": planned}]}]
    printed = capsys.readouterr().out
    assert "plan shot 1: close-up on her face, slow push in, plays 3 s" in printed
    assert "!!" not in printed
