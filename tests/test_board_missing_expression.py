"""The board gate warns when an emotional row has no expression picked.

The server prints a row's ``reaction_kind`` into the take as a picture of the
face (fictora-drama: expression captions under Fal's ``quality`` rewrite). A
row without one plays its emotion from the motion text alone, which the
rewrite thins or drops. Hanakaze Sweets ep 1 (desk 2026-09-26) is the shape:
beat 3 ends on "Eyes open with tiny trembling pupils and a twitching
eyelid" (a smiling rage) and its row, "Customer Smile" / "Rage Smile", wears
no expression.
"""

from __future__ import annotations

from typing import Any

from creation.spine_view import missing_expression_lines


def _frame(
    ordinal: int,
    row: int,
    *,
    role: str = "anchor",
    scale: str = "medium shot",
    kind: str | None = None,
    staged: bool = True,
) -> dict[str, Any]:
    return {
        "frame_id": f"frame_episode_01_{ordinal:02d}",
        "ordinal": ordinal,
        "board_row": row,
        "visual_brief": {
            "subject_blocking": [{"cast_id": "cast_mitsu", "pose": "grins"}]
            if staged
            else [],
            "cell_role": role,
            "shot_scale": scale,
            "reaction_kind": kind,
        },
    }


def _beat(
    ordinal: int,
    frame: int,
    *,
    end_state: str = "",
    payoff: str = "none",
    kind: str | None = None,
) -> dict:
    return {
        "beat_id": f"beat_{ordinal}",
        "ordinal": ordinal,
        "frame_id": f"frame_episode_01_{frame:02d}",
        "motion_direction": {"end_state": end_state},
        "satisfaction_type": payoff,
        "reaction_kind": kind,
    }


def test_the_hanakaze_rage_smile_row_is_named_with_its_beat() -> None:
    frames = [_frame(7, 4), _frame(8, 4, role="action", scale="medium close-up")]
    beats = [
        _beat(
            3,
            7,
            end_state="Eyes open with tiny trembling pupils and a twitching eyelid",
            payoff="comeback",
        )
    ]

    (line,) = missing_expression_lines(frames, beats, episode=1)

    assert line.startswith("  !! row 4: an emotional moment (")
    assert "beat 3 ends on 'Eyes open with tiny trembling pupils" in line
    assert "beat 3 pays off a comeback" in line
    assert "frame_episode_01_08 is a medium close-up on a face" in line
    assert "`edit --episode 1 --beat 3 --expression KIND`" in line
    assert "warning only" in line


def test_a_row_that_wears_an_expression_is_quiet() -> None:
    frames = [
        _frame(7, 4, kind="smiling_rage"),
        _frame(8, 4, role="action", scale="medium close-up"),
    ]
    beats = [_beat(3, 7, end_state="Eyes open with tiny trembling pupils")]
    assert missing_expression_lines(frames, beats, episode=1) == []


def test_a_beat_that_asked_for_one_is_left_to_the_shot_list() -> None:
    frames = [_frame(7, 4)]
    beats = [_beat(3, 7, end_state="She freezes", kind="freeze")]
    assert missing_expression_lines(frames, beats, episode=1) == []


def test_a_reaction_cell_with_a_face_is_an_emotional_moment_without_a_beat() -> None:
    frames = [_frame(1, 1), _frame(2, 2, role="reaction", scale="wide shot")]
    beats = [_beat(1, 1)]

    (line,) = missing_expression_lines(frames, beats, episode=2)

    assert line.startswith(
        "  !! row 2: an emotional moment (frame_episode_01_02 is a reaction cell)"
    )
    # The row continues beat 1's shot: that is the beat to edit.
    assert "--beat 1 --expression KIND" in line


def test_a_wide_calm_row_and_a_faceless_insert_are_not_flagged() -> None:
    frames = [
        _frame(1, 1, scale="wide establishing shot"),
        _frame(2, 2, role="insert", scale="extreme close-up", staged=False),
        _frame(3, 3, role="reaction", staged=False),
    ]
    beats = [_beat(1, 1)]
    assert missing_expression_lines(frames, beats, episode=1) == []


def test_the_board_gate_shot_list_carries_the_warning() -> None:
    from creation.spine_view import shot_list_lines
    from fake_api import spine_fixture

    spine = spine_fixture()
    assert not any(
        "no expression picked" in line for line in shot_list_lines(spine, episode=1)
    )
    spine["frames"][0]["visual_brief"]["cell_role"] = "reaction"
    spine["frames"][0]["visual_brief"]["reaction_kind"] = None

    board = shot_list_lines(spine, episode=1)

    assert any(
        line.startswith("  !! row 1: an emotional moment")
        and "no expression picked" in line
        for line in board
    )
