"""Who is on screen per shot: the take facts' optional ``shots[].people`` printed after filming, by
``take-facts`` and in ``review``'s People section, and the board's warning when one character changes
posture right at a cut (Turbo can draw them twice from the board).

Every printout must say nothing extra when the server sent no ``people`` (older servers).
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

from creation import episode_commands as ec
from creation.post.review import NONE, people_section
from creation.post.take_facts import (
    cast_names_from,
    has_people,
    shot_people,
    shot_people_lines,
)
from creation.spine_view import (
    pose_change_at_cut_lines,
    pose_posture,
    shot_list_lines,
)
from fake_api import FakeApi, spine_fixture

NAMES = {"cast_hana": "Hana", "cast_ren": "Ren"}


def _facts(*, people: bool) -> dict[str, Any]:
    shots: list[dict[str, Any]] = [
        {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 4.8, "speaks": True},
        {"shot_index": 2, "start_seconds": 4.8, "end_seconds": 7.7, "speaks": False},
    ]
    if people:
        shots[0]["people"] = {"count": 2, "named": ["cast_hana"], "unnamed": 1}
        shots[1]["people"] = {"count": 1, "named": ["cast_ren"], "unnamed": 0}
    return {"job_id": "job_take_1", "shots": shots, "sfx_cues": []}


# --- the per-shot line ------------------------------------------------------------------------------


def test_each_shot_with_a_head_count_says_who_is_on_screen_by_name() -> None:
    assert shot_people_lines(_facts(people=True), NAMES) == [
        "shot 1 (0.00-4.80s): On screen: 2 people (Hana, + 1 unnamed)",
        "shot 2 (4.80-7.70s): On screen: 1 person (Ren)",
    ]
    assert has_people({"take_facts": _facts(people=True)})


def test_facts_from_an_older_server_print_nothing() -> None:
    assert shot_people_lines(_facts(people=False), NAMES) == []
    assert not has_people(_facts(people=False))
    assert shot_people_lines(None, NAMES) == []


def test_an_unknown_cast_id_prints_as_itself_and_a_bad_field_is_ignored() -> None:
    assert shot_people({"people": {"count": 1, "named": ["cast_x"]}}) == {
        "count": 1,
        "named": ["cast_x"],
        "unnamed": 0,
    }
    assert shot_people({"people": {"named": ["cast_x"]}}) is None
    assert shot_people({"people": {"count": True}}) is None
    assert shot_people({"people": "two"}) is None
    facts = {"shots": [{"shot_index": 1, "people": {"count": 1, "named": ["cast_x"]}}]}
    assert shot_people_lines(facts, NAMES) == ["shot 1: On screen: 1 person (cast_x)"]


def test_cast_names_come_from_the_spine() -> None:
    assert cast_names_from(spine_fixture()) == NAMES
    assert cast_names_from(None) == {}


# --- review: People ---------------------------------------------------------------------------------


def test_review_people_lists_the_expected_counts_and_asks_for_a_human_check() -> None:
    section = people_section(_facts(people=True), NAMES)

    assert section.status == NONE
    text = "\n".join(section.lines())
    assert "shot 1 (0.00-4.80s): On screen: 2 people (Hana, + 1 unnamed)" in text
    assert "is anyone on screen twice" in text
    assert "no face detector" in text
    assert "same person rendered twice at shot N" in text


def test_review_people_says_in_one_line_when_the_server_sent_no_counts() -> None:
    section = people_section(_facts(people=False), NAMES)

    assert section.lines() == [
        "– People: the server didn't send head counts for this take (older server): count by eye  "
        "[head counts from the take facts; counted by eye (no face detector here)]"
    ]
    assert people_section(None, NAMES).summary == "no take facts on the desk"


# --- take-facts -------------------------------------------------------------------------------------


def _saved(desk: Path, facts: dict[str, Any]) -> None:
    api_dir = desk / "ep01" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "spine.json").write_text(json.dumps(spine_fixture()), encoding="utf-8")
    (api_dir / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts))
    (api_dir / "17_raw_scene_clips.json").write_text(
        json.dumps({"clips": [{"episode_id": "episode_01", "set_index": 1,
                               "job_id": "job_take_1", "url": "https://r2.example/t1.mp4"}]})
    )  # fmt: skip


def test_take_facts_prints_who_is_on_screen_with_names_from_the_saved_spine(
    desk: Path,
) -> None:
    _saved(desk, _facts(people=True))
    out = io.StringIO()

    ec.run_take_facts(desk, episode=1, take_id="t1", out=out)

    assert (
        "  shot 1 (0.00-4.80s): On screen: 2 people (Hana, + 1 unnamed)"
        in out.getvalue()
    )
    assert "  shot 2 (4.80-7.70s): On screen: 1 person (Ren)" in out.getvalue()


def test_take_facts_without_people_says_nothing_about_who_is_on_screen(
    desk: Path,
) -> None:
    _saved(desk, _facts(people=False))
    out = io.StringIO()

    ec.run_take_facts(desk, episode=1, take_id="t1", out=out)

    assert "On screen" not in out.getvalue()


def test_a_refresh_prints_who_is_on_screen_in_the_fresh_facts(
    desk: Path, api: FakeApi
) -> None:
    _saved(desk, _facts(people=False))
    api.routes[("GET", "/v1/jobs/job_take_1/take-facts")] = {
        "take_facts": _facts(people=True)
    }
    out = io.StringIO()

    ec.run_take_facts(desk, episode=1, take_id="t1", refresh=True, out=out)

    assert "shot 2 (4.80-7.70s): On screen: 1 person (Ren)" in out.getvalue()


# --- the board: one character, two postures, right at a cut ---------------------------------------


def _frame(ordinal: int, row: int, *blocking: tuple[str, str]) -> dict[str, Any]:
    return {
        "frame_id": f"frame_{ordinal:02d}",
        "ordinal": ordinal,
        "board_row": row,
        "cast_refs": [cast_id for cast_id, _ in blocking],
        "visual_brief": {
            "subject_blocking": [
                {"cast_id": cast_id, "frame_position": "center", "pose": pose}
                for cast_id, pose in blocking
            ]
        },
    }


def _board(
    last_of_row_3: str, first_of_row_4: str, *, who: str = "cast_ren"
) -> list[dict[str, Any]]:
    return [
        _frame(5, 3, ("cast_ren", "standing by the door")),
        _frame(6, 3, ("cast_ren", last_of_row_3)),
        _frame(7, 4, (who, first_of_row_4)),
        _frame(8, 4, ("cast_hana", "sitting at the counter")),
    ]


def test_a_character_kneeling_then_standing_across_a_cut_is_warned() -> None:
    lines = pose_change_at_cut_lines(
        _board("kneeling over the broken cup", "stands up, fists clenched"),
        cast_names=NAMES,
    )

    assert len(lines) == 1
    assert (
        "!! rows 3 and 4: Ren is kneeling in row 3 cell 2 and standing in row 4 cell 1"
        in lines[0]
    )
    assert "count the people at that cut" in lines[0]


def test_no_warning_for_the_same_posture_an_unplain_pose_or_another_character() -> None:
    same = _board("kneeling over the cup", "on his knees, head bowed")
    vague = _board("kneeling over the cup", "turns toward the window")
    both = _board("kneeling over the cup", "rises from kneeling to standing")
    other = _board("kneeling over the cup", "standing at the till", who="cast_hana")

    for frames in (same, vague, both, other):
        assert pose_change_at_cut_lines(frames, cast_names=NAMES) == []


def test_a_posture_change_inside_one_row_is_not_a_cut() -> None:
    frames = [
        _frame(1, 1, ("cast_ren", "kneeling")),
        _frame(2, 1, ("cast_ren", "standing")),
    ]
    assert pose_change_at_cut_lines(frames, cast_names=NAMES) == []


def test_pose_posture_reads_one_plain_posture_only() -> None:
    assert pose_posture("Motionless face-down on the concrete") == "lying"
    assert pose_posture("seated on the bench") == "sitting"
    assert pose_posture("hands raised, eyes wide") is None
    assert pose_posture("crouching, then standing") is None


def test_the_board_printout_carries_the_posture_warning() -> None:
    spine = copy.deepcopy(spine_fixture())
    frames = [f for f in spine["frames"] if f["episode_id"] == "episode_01"]
    frames[0]["visual_brief"]["subject_blocking"][0]["pose"] = "kneeling by the till"
    frames[1]["visual_brief"]["subject_blocking"][0]["pose"] = "standing by the door"

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert (
        "!! rows 1 and 2: Hana is kneeling in row 1 cell 1 and standing in row 2 cell 1"
        in lines
    )
