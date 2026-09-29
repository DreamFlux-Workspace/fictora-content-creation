"""Preflight: human gates block (exit 3); quality checks and budgets warn (exit 4) until confirmed per unit."""

from __future__ import annotations

from pathlib import Path

import pytest

from creation.cli_ops import main as ops_main
from creation.ops.floor import (
    approve_board,
    approve_script,
    approve_series_gate,
    confirm_preflight,
    init_series_desk,
    preflight_take,
    record_estimate,
    record_spend,
    set_take_lines,
)
from creation.ops.state import SpokenLine, episode_by_ordinal, load_series
from fake_api import png_bytes


@pytest.fixture
def ready_desk(tmp_path: Path) -> Path:
    desk = init_series_desk(tmp_path, "Warn Test", band="15s", episode_count=1)
    approve_series_gate(desk, "look")
    approve_series_gate(desk, "plates")
    approve_script(desk, episode=1)
    board = desk / "ep01" / "boards" / "board-ep01-t1-v1.png"
    board.write_bytes(png_bytes(gray=15))  # a very dark board
    approve_board(desk, episode=1, take_id="t1", image=board)
    record_estimate(desk, episode=1, take_id="t1", usd=1.20)
    return desk


def test_a_dark_board_is_information_not_a_warning(ready_desk: Path) -> None:
    report = preflight_take(ready_desk, episode=1, take_id="t1")
    luma = next(check for check in report.checks if check.code == "board_luma")
    assert luma.ok and "brightness" in luma.detail
    assert report.status() == "PASS"


def test_past_twice_the_envelope_warns_and_a_named_confirmation_clears_one_film(
    ready_desk: Path,
) -> None:
    record_spend(
        ready_desk, episode=1, usd=10.0
    )  # $11.20 projected on a $5.50 first-episode envelope
    report = preflight_take(ready_desk, episode=1, take_id="t1")
    assert not report.blocked and not report.cleared and report.status() == "WARN"
    assert "$5.50 envelope" in next(c.detail for c in report.warnings())
    assert (
        ops_main(
            ["preflight", "--desk", str(ready_desk), "--episode", "1", "--take", "t1"]
        )
        == 4
    )

    with pytest.raises(ValueError, match="must name this unit exactly: ep01-t1"):
        confirm_preflight(ready_desk, episode=1, take_id="t1", confirm_unit="ep01-t2")
    code = ops_main(
        [
            "preflight",
            "--desk",
            str(ready_desk),
            "--episode",
            "1",
            "--take",
            "t1",
            "--proceed-anyway",
            "ep01-t1",
        ]
    )
    assert code == 0
    take = episode_by_ordinal(load_series(ready_desk), 1).takes[0]
    assert (
        take.overrides[0].codes == ["envelope"] and take.overrides[0].filmed_count == 0
    )


def test_an_open_gate_blocks_and_cannot_be_overridden(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Gate Test", band="15s", episode_count=1)
    assert (
        ops_main(["preflight", "--desk", str(desk), "--episode", "1", "--take", "t1"])
        == 3
    )
    with pytest.raises(ValueError, match="cannot be overridden"):
        confirm_preflight(desk, episode=1, take_id="t1", confirm_unit="ep01-t1")


def test_four_lines_are_approved_with_a_warning_not_refused(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Lines Test", band="15s", episode_count=1)
    set_take_lines(
        desk,
        episode=1,
        take_id="t1",
        lines=[SpokenLine("Hana", f"line {n}") for n in range(4)],
    )
    record = approve_script(desk, episode=1)
    assert record.status == "approved" and record.note.startswith(
        "WARNING: t1 has 4 lines"
    )


# --- hand-off frame on a new episode (Hanakaze ep 2-3: warned on every episode 2 on) ---------------


def _two_episodes(tmp_path: Path, *, ep1_location: str, ep2_location: str) -> Path:
    import json

    desk = init_series_desk(tmp_path, "Handoff Test", band="15s", episode_count=2)
    spine = {
        "episode_summaries": [
            {"episode_id": "episode_01", "ordinal": 1},
            {"episode_id": "ep_02", "ordinal": 2},
        ],
        "beats": [{"beat_id": "b1", "episode_id": "ep_02"}],
        "frames": [
            {"frame_id": "f1", "episode_id": "episode_01", "ordinal": 1,
             "visual_brief": {"location": "the arcade street"}},
            {"frame_id": "f2", "episode_id": "episode_01", "ordinal": 2,
             "visual_brief": {"location": ep1_location}},
            {"frame_id": "f3", "episode_id": "ep_02", "ordinal": 1,
             "visual_brief": {"location": ep2_location}},
        ],
    }  # fmt: skip
    api = desk / "ep02" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "spine.json").write_text(json.dumps(spine), encoding="utf-8")
    return desk


def _handoff(desk: Path, episode: int, take_id: str = "t1"):
    report = preflight_take(desk, episode=episode, take_id=take_id)
    return next(check for check in report.checks if check.code == "handoff")


def test_a_new_episode_on_a_new_scene_needs_no_hand_off_frame(tmp_path: Path) -> None:
    desk = _two_episodes(
        tmp_path,
        ep1_location="inside the sweet shop",
        ep2_location="Ren's bakery doors",
    )

    check = _handoff(desk, 2)

    assert check.ok, "an info line, never a warning"
    assert check.detail.startswith("info: no hand-off frame")
    assert (
        "It opens on a new scene (Ren's bakery doors; episode 1 ended in inside the sweet shop)"
        in check.detail
    )
    assert "fictora-ops handoff --desk D --episode 2 --take t1" in check.detail


def test_a_new_episode_where_the_last_one_ended_is_still_only_information(
    tmp_path: Path,
) -> None:
    desk = _two_episodes(
        tmp_path,
        ep1_location="inside the sweet shop",
        ep2_location="Inside the  sweet shop",
    )

    check = _handoff(desk, 2)

    assert check.ok and "It opens where episode 1 ended" in check.detail


def test_without_a_saved_spine_the_new_episode_is_information_too(
    tmp_path: Path,
) -> None:
    desk = init_series_desk(tmp_path, "No Spine", band="15s", episode_count=2)
    check = _handoff(desk, 2)
    assert (
        check.ok
        and "does not say episode 2 continues straight from episode 1" in check.detail
    )


def test_a_later_take_of_an_episode_still_warns_without_a_hand_off(
    tmp_path: Path,
) -> None:
    from creation.ops.state import TakeState, save_series

    desk = init_series_desk(tmp_path, "Two Takes", band="15s", episode_count=1)
    series = load_series(desk)
    slot = episode_by_ordinal(series, 1)
    slot.takes.append(TakeState(take_id="t2"))
    save_series(desk, series)

    check = _handoff(desk, 1, "t2")

    assert (
        not check.ok
        and check.detail == "Hand-off frame is not set. Paste the previous last frame."
    )
