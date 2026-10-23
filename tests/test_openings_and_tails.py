"""Openings and endings of a finished episode: dark / static / faceless openings, a settled tail, close-ups.

(Kept from the retired reel tests, 23 Oct 2026: these read the episode's own opening and ending.)
"""

from __future__ import annotations

import pytest

from creation.post.faces import FaceReading, closeup_score
from creation.post.opening import (
    STATIC_MOTION,
    opening_reading,
    silent_opening,
    tail_reading,
    tail_warning,
)


def test_closeup_score_grows_with_the_face() -> None:
    assert closeup_score(0.0) == 0.0
    assert closeup_score(0.015) == pytest.approx(0.5)
    assert closeup_score(0.2) == 1.0
    assert FaceReading(0, 0.3).score == 0.0


def test_opening_reading_flags_a_dark_static_faceless_open() -> None:
    reading = opening_reading(
        [0.03] * 9, [0.001] * 9, face=FaceReading(0, 0.0), face_source="detector"
    )
    joined = " ".join(reading.warnings)
    assert "opens dark" in joined and "is static" in joined and "no face" in joined


def test_a_lit_moving_open_on_a_face_is_clean_and_a_silent_open_skips_the_face() -> (
    None
):
    lit = opening_reading(
        [0.4] * 9, [0.03] * 9, face=FaceReading(1, 0.05), face_source="detector"
    )
    assert lit.warnings == ()
    silent = opening_reading(
        [0.4] * 9, [0.03] * 9, face=0.0, face_source="head_count", silent_open=True
    )
    assert silent.warnings == ()


def test_a_dark_frame_zero_that_lights_up_at_once_is_not_dark() -> None:
    reading = opening_reading([0.02, *[0.5] * 8], [0.05] * 9)
    assert not any("dark" in w for w in reading.warnings)


def test_silent_opening_reads_the_first_beat() -> None:
    spine = {
        "episode_summaries": [{"episode_id": "episode_02", "ordinal": 2}],
        "beats": [
            {"episode_id": "episode_02", "ordinal": 1, "dialogue_lines": []},
            {"episode_id": "episode_02", "ordinal": 2, "dialogue_lines": [{"text": "Hi"}]},
        ],
    }  # fmt: skip
    assert silent_opening(spine, 2) is True
    spine["beats"][0]["dialogue_lines"] = [{"text": "Who's there?"}]
    assert silent_opening(spine, 2) is False


def test_tail_reading_counts_motion_as_action() -> None:
    times = [i / 8 for i in range(40)]
    moving = [0.02 if t < 4.5 else 0.0 for t in times]
    reading = tail_reading(times, moving, end=5.0, last_mark=2.0)
    assert reading.settled_seconds == pytest.approx(5.0 - 4.375)
    assert tail_warning(reading) is not None
    busy = tail_reading(times, [0.02] * 40, end=5.0, last_mark=2.0)
    assert tail_warning(busy) is None
    assert STATIC_MOTION < 0.02
