"""The board gate warns when a character's mood jumps between rows with no cause on screen.

Hanakaze Sweets ep 7 "Not For Sale" (2026-10-01, board
ep07/boards/board-ep07-t1-v1.png): rows 1-2 play Mitsu in ``comic_anger``;
row 3 (Ren politely asks to learn, scripted "her raised finger falters")
played ``eye_twitch`` and was drawn calm, thinking "hmm"; row 4 she shouts
again and then goes ``stunned_blank`` at the clue on his cuff. The frames
below carry ep 7's kinds and faces as the approved spine had them.
"""

from __future__ import annotations

from typing import Any

from creation.mood_jump import MOOD_FAMILY, mood_jump_lines, mood_jumps
from creation.spine_view import shot_list_lines
from fake_api import spine_fixture

_MITSU = "cast_mitsu-hanakaze"
_REN = "cast_ren-kurosawa"
_NAMES = {_MITSU: "Mitsu Hanakaze", _REN: "Ren Kurosawa"}


def _frame(
    ordinal: int,
    row: int,
    kind: str | None,
    *,
    cast: tuple[str, ...] = (_MITSU, _REN),
    owner: str | None = _MITSU,
    cause: str | None = None,
    episode: str = "episode_07",
) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "story_moment": "Mitsu and Ren face each other across the glass wagashi case.",
        "subject_blocking": [
            {
                "cast_id": c,
                "frame_position": "behind the case",
                "pose": "eyes on the other",
                "interaction": "hands on the glass",
            }
            for c in cast
        ],
        "reaction_kind": kind,
        "shot_scale": "medium two-shot",
        "camera_angle": "eye level",
    }
    if kind is not None and len(cast) > 1 and owner is not None:
        brief["reaction_cast_id"] = owner
    if cause is not None:
        brief["expression_cause"] = cause
    return {
        "frame_id": f"frame_{episode}_{ordinal:02d}",
        "episode_id": episode,
        "ordinal": ordinal,
        "board_row": row,
        "cast_refs": list(cast),
        "visual_brief": brief,
    }


def _ep7() -> list[dict[str, Any]]:
    return [
        _frame(1, 1, "comic_anger", cast=(_MITSU,)),
        _frame(2, 1, "comic_anger", cast=(_MITSU,)),
        _frame(3, 2, "comic_anger"),
        _frame(4, 2, "comic_anger"),
        _frame(5, 3, "eye_twitch"),
        _frame(6, 3, "eye_twitch"),
        _frame(7, 4, "stunned_blank"),
        _frame(8, 4, "stunned_blank"),
    ]


def test_ep7_row_three_is_one_warning_with_the_rows_named() -> None:
    lines = mood_jump_lines(_ep7(), cast_names=_NAMES)

    assert len(lines) == 1, lines
    (line,) = lines
    assert line.startswith(
        "  !! rows 2→3: Mitsu Hanakaze's expression jumps from comic anger (angry) to eye twitch"
    )
    assert "edit --frame 5" in line
    assert "expression_cause" in line
    assert "warning only" in line


def test_a_shock_is_a_reaction_not_a_jump() -> None:
    (jump,) = mood_jumps(_ep7())

    assert (jump.from_row, jump.to_row, jump.to_kind) == (2, 3, "eye_twitch")
    assert MOOD_FAMILY["stunned_blank"] == "shocked"


def test_rage_calm_rage_is_reported_as_one_bounce() -> None:
    frames = _ep7()
    frames[6] = _frame(7, 4, "comic_anger")
    frames[7] = _frame(8, 4, "comic_anger")

    (line,) = mood_jump_lines(frames, cast_names=_NAMES)

    assert line.startswith(
        "  !! rows 2→3→4: Mitsu Hanakaze's mood bounces comic anger → eye twitch → comic anger"
    )
    assert "row 3 resets the angry mood" in line
    assert "edit --frame 5" in line


def test_a_stated_cause_silences_the_warning() -> None:
    frames = _ep7()
    frames[4] = _frame(5, 3, "eye_twitch", cause="Ren's deep polite bow")

    assert mood_jump_lines(frames, cast_names=_NAMES) == []


def test_the_same_mood_played_differently_is_not_a_jump() -> None:
    frames = _ep7()
    frames[4] = _frame(5, 3, "anger_flare")
    frames[5] = _frame(6, 3, "smiling_rage")

    assert mood_jumps(frames) == []


def test_a_face_the_spine_does_not_name_is_not_guessed() -> None:
    frames = _ep7()
    frames[4] = _frame(5, 3, "eye_twitch", owner=None)
    frames[5] = _frame(6, 3, "eye_twitch", owner=None)

    assert mood_jumps(frames) == []


def test_the_shot_list_at_the_board_gate_carries_it() -> None:
    spine = spine_fixture()
    template = next(f for f in spine["frames"] if f.get("episode_id") == "episode_01")
    spine["cast"] = [
        {"cast_id": cast_id, "name": name} for cast_id, name in _NAMES.items()
    ]
    ep1 = [
        {
            **template,
            **frame,
            "frame_id": f"frame_episode_01_{frame['ordinal']:02d}",
            "episode_id": "episode_01",
        }
        for frame in _ep7()
    ]
    spine["frames"] = [
        f for f in spine["frames"] if f.get("episode_id") != "episode_01"
    ] + ep1

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert "!! rows 2→3: Mitsu Hanakaze's expression jumps from comic anger" in lines
