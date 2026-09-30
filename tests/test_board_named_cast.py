"""The board gate warns when a frame's words name one cast member but its cast list has another.

Hanakaze Sweets ep 4 (2026-09-30): frames 4, 6 and 8 said "Catches Ren lowering
his phone" and "Ren and pastry counter background right" but listed Genzō, and
the board drew an old man in a brown robe in two cells.
"""

from __future__ import annotations

from typing import Any

from creation.spine_view import named_cast_mismatch_lines, shot_list_lines
from fake_api import spine_fixture

_NAMES = {
    "cast_mitsu": "Mitsu Hanakaze",
    "cast_genzo": "Genzō Hanakaze",
    "cast_ren": "Ren Kurosawa",
}


def _frame(
    listed: list[str], *, moment: str, depth: str, frame_id: str = "frame_episode_04_06"
) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "ordinal": 6,
        "board_row": 3,
        "beat_label": "Friction: Ren Is Caught",
        "beat_prompt": "Mitsu notices a small smile over the meme.",
        "cast_refs": listed,
        "visual_brief": {
            "story_moment": moment,
            "depth_order": depth,
            "forbidden_elements": ["no visible Genzō Hanakaze"],
        },
    }


_EP4 = {
    "moment": "Catches Ren lowering his phone as his small smile disappears.",
    "depth": "Mitsu frame left; raised phones central midground; Ren frame right beside his counter",
}


def test_a_frame_that_names_ren_but_lists_genzo_is_warned() -> None:
    lines = named_cast_mismatch_lines(
        [_frame(["cast_mitsu", "cast_genzo"], **_EP4)], cast_names=_NAMES
    )

    assert lines == [
        "  !! frame_episode_04_06 (row 3) names Ren Kurosawa but lists Genzō Hanakaze: the board draws whom the "
        "cast list names. Edit that frame's cast to the character it describes, then redraw (warning only)."
    ]


def test_the_corrected_frame_is_not_warned() -> None:
    assert (
        named_cast_mismatch_lines(
            [_frame(["cast_mitsu", "cast_ren"], **_EP4)], cast_names=_NAMES
        )
        == []
    )


def test_a_shared_surname_or_a_forbidden_element_names_nobody() -> None:
    frame = _frame(
        ["cast_mitsu", "cast_ren"],
        moment="Mitsu yanks up Hanakaze Sweets' shutter.",
        depth="Mitsu foreground; queue midground",
    )
    frame["beat_label"] = "Opening"

    assert named_cast_mismatch_lines([frame], cast_names=_NAMES) == []


def test_a_member_placed_off_frame_is_not_drawn_so_not_warned() -> None:
    frame = _frame(["cast_mitsu"], **_EP4)
    frame["visual_brief"]["subject_blocking"] = [
        {"cast_id": "cast_mitsu", "frame_position": "frame left"},
        {"cast_id": "cast_genzo", "frame_position": "off-frame, upstairs"},
    ]

    assert named_cast_mismatch_lines([frame], cast_names=_NAMES) == []


def test_a_speaker_anchored_on_the_frame_belongs_on_its_list() -> None:
    """Hanakaze ep 1 frame 5: Genzō's line plays over Mitsu's hands while Ren's queue sits behind."""

    frame = _frame(
        ["cast_genzo", "cast_mitsu"],
        moment="Her two hands clamp the sign while Ren's long patisserie queue sits blurred behind it.",
        depth="Mitsu's hands foreground",
    )
    beats = [
        {
            "frame_id": "frame_episode_04_06",
            "dialogue_lines": [{"cast_id": "cast_genzo", "text": "Smile!"}],
        }
    ]

    assert named_cast_mismatch_lines([frame], beats, cast_names=_NAMES) == []
    assert named_cast_mismatch_lines([frame], cast_names=_NAMES) != []


def test_the_shot_list_carries_the_warning() -> None:
    spine = spine_fixture()
    names = {card["cast_id"]: card["name"] for card in spine["cast"]}
    frame = spine["frames"][0]
    listed = {entry["cast_id"] for entry in frame["visual_brief"]["subject_blocking"]}
    other = next(cast_id for cast_id in names if cast_id not in listed)
    frame["visual_brief"]["story_moment"] = f"{names[other]} steps into the doorway."
    frame["visual_brief"]["depth_order"] = "doorway midground"
    frame["beat_label"] = "Arrival"
    frame["beat_prompt"] = "Someone arrives."
    for beat in spine["beats"]:
        if beat.get("frame_id") == frame["frame_id"]:
            beat["frame_id"] = "frame_elsewhere"

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert f"names {names[other]} but lists" in lines
