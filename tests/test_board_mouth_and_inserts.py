"""The board gate warns when a speaking cell hides the mouth, and when inserts run two in a row.

Hanakaze Sweets ep 6 (2026-09-30, board ep06/boards/board-ep06-t1-v1.png):
frame 01 carried Mitsu's shrieked line, its pose said "mouth clearing after
the spray" and it played ``dramatic_gasp``; the board drew her hand clamped over
her mouth on both cells of the speaking row. Row 2 (frames 03 and 04) was two
countertop inserts, about 3.5 s with no face, right after the hook.
"""

from __future__ import annotations

from typing import Any

import pytest

from creation.spine_view import (
    MOUTH_HIDDEN,
    insert_run_lines,
    shot_list_lines,
    speaking_mouth_hidden_lines,
)
from fake_api import spine_fixture

_NAMES = {"cast_mitsu": "Mitsu Hanakaze", "cast_ren": "Ren Kurosawa"}


def _frame(
    ordinal: int,
    row: int,
    *,
    pose: str = "eyes wide",
    role: str = "anchor",
    scale: str = "extreme close-up",
    kind: str | None = None,
    cast: tuple[str, ...] = ("cast_mitsu",),
    chain: tuple[str, ...] = (),
) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "story_moment": "Tea sprays sideways as Mitsu jerks back from the heart-framed phone.",
        "subject_blocking": [
            {
                "cast_id": c,
                "frame_position": "center",
                "pose": pose,
                "interaction": "holds the phone upright",
            }
            for c in cast
        ],
        "cell_role": role,
        "shot_scale": scale,
        "reaction_kind": kind,
    }
    if chain:
        brief["row_direction"] = {
            "starts": "Mitsu sips from the cup.",
            "chain": list(chain),
            "ends": "She recoils.",
        }
    return {
        "frame_id": f"frame_episode_06_{ordinal:02d}",
        "ordinal": ordinal,
        "board_row": row,
        "cast_refs": list(cast),
        "visual_brief": brief,
    }


def _ep6() -> list[dict[str, Any]]:
    return [
        _frame(
            1,
            1,
            pose="eyes stretched wide, brows lifted high, mouth clearing after the spray, shoulders recoiling",
            kind="dramatic_gasp",
            chain=(
                "Her eyes widen as tea sprays sideways.",
                "She lowers the empty cup and clears her mouth.",
            ),
        ),
        _frame(
            2,
            1,
            pose="eyes wide, lips parted after the shout",
            role="action",
            kind="dramatic_gasp",
        ),
        _frame(
            3,
            2,
            role="insert",
            scale="insert shot",
            pose="fingers splayed beside the cup",
        ),
        _frame(
            4,
            2,
            role="action",
            scale="insert shot",
            pose="fingers lifted sharply from the spill",
        ),
        _frame(
            5,
            3,
            role="anchor",
            scale="wide establishing shot",
            cast=("cast_mitsu", "cast_ren"),
        ),
        _frame(
            6,
            3,
            role="action",
            scale="wide establishing shot",
            cast=("cast_mitsu", "cast_ren"),
        ),
    ]


_BEATS = [
    {
        "frame_id": "frame_episode_06_01",
        "dialogue_lines": [
            {"cast_id": "cast_mitsu", "text": "Couple?! What?!", "off_screen": None}
        ],
    },
    {"frame_id": "frame_episode_06_05", "dialogue_lines": []},
]


def test_ep6_speaking_row_is_warned_on_both_cells() -> None:
    lines = speaking_mouth_hidden_lines(_ep6(), _BEATS, cast_names=_NAMES)

    assert len(lines) == 2
    assert lines[0].startswith(
        "  !! frame_episode_06_01 (row 1) carries Mitsu Hanakaze's line but its staging hides"
    )
    assert "mouth clearing" in lines[0] and "clears her mouth" in lines[0]
    assert "dramatic_gasp" in lines[0]
    assert lines[1].startswith("  !! frame_episode_06_02 (row 1)")
    assert all(line.endswith("(warning only).") for line in lines)


def test_a_free_mouth_an_off_screen_line_or_a_silent_row_is_not_warned() -> None:
    clean = [
        _frame(1, 1, pose="mouth wide open mid-shriek, eyes bulging"),
        _frame(2, 1),
    ]
    assert speaking_mouth_hidden_lines(clean, _BEATS, cast_names=_NAMES) == []

    off = [
        {
            **_BEATS[0],
            "dialogue_lines": [
                {"cast_id": "cast_mitsu", "text": "Hey!", "off_screen": True}
            ],
        }
    ]
    assert speaking_mouth_hidden_lines(_ep6(), off, cast_names=_NAMES) == []
    # Row 2 hides nothing it speaks: no line is anchored there.
    silent = [_frame(3, 2, pose="hand over her mouth")]
    assert speaking_mouth_hidden_lines(silent, _BEATS, cast_names=_NAMES) == []


@pytest.mark.parametrize(
    "text",
    [
        "one hand over the mouth",
        "mouth open behind the hand",
        "covers her mouth with both hands",
        "the teacup still at her lips",
        "wipes tea from her chin",
        "mouth full of mochi",
    ],
)
def test_mouth_hiding_words(text: str) -> None:
    assert MOUTH_HIDDEN.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "mouth wide open mid-shriek",
        "no hand near her face",
        "Mitsu sips from the cup while scrolling",
        "hands clear of her mouth",
    ],
)
def test_free_mouth_words(text: str) -> None:
    assert not MOUTH_HIDDEN.search(text)


def test_ep6_two_inserts_in_a_row_are_warned() -> None:
    lines = insert_run_lines(_ep6())

    assert lines == [
        "  !! 2 insert cells in a row (frame_episode_06_03, frame_episode_06_04; rows 2): about 3.5 s with no "
        "face. Keep one insert and make the other a reaction close-up (warning only)."
    ]


def test_one_insert_or_inserts_apart_are_not_warned() -> None:
    frames = _ep6()
    frames[3] = _frame(4, 2, role="reaction", scale="close-up")
    assert insert_run_lines(frames) == []
    frames[5] = _frame(6, 3, role="cover", scale="abstract insert")
    assert insert_run_lines(frames) == []


def test_the_shot_list_carries_both_warnings() -> None:
    spine = spine_fixture()
    first, second = (f for f in spine["frames"] if f.get("episode_id") == "episode_01")
    first["visual_brief"]["subject_blocking"][0]["pose"] = (
        "one hand clamped over her mouth"
    )
    spine["beats"][0]["frame_id"] = first["frame_id"]
    third = {
        **second,
        "frame_id": "frame_episode_01_03",
        "ordinal": 3,
        "visual_brief": {
            **second["visual_brief"],
            "cell_role": "insert",
            "shot_scale": "insert shot",
        },
    }
    second["visual_brief"] = {
        **second["visual_brief"],
        "cell_role": "insert",
        "shot_scale": "insert shot",
    }
    spine["frames"].insert(spine["frames"].index(second) + 1, third)

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert "carries Hana" in lines and "hides the mouth" in lines
    assert "insert cells in a row" in lines
