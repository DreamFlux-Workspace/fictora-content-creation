"""Reel openers and endings: the cold open never spoils the ending, faces and stillness by genre,
dark / static / faceless openings, endings on the peak, the optional freeze-black, one hashtag per genre."""

from __future__ import annotations

import json

import pytest

from creation.captions import Cue
from creation.post.faces import HEAD_COUNT_FACE, FaceReading, closeup_score
from creation.post.opening import (
    DARK_LUMA,
    STATIC_MOTION,
    TAIL_MAX_SECONDS,
    opening_reading,
    silent_opening,
    tail_reading,
    tail_warning,
)
from creation.post.reel_plan import (
    PICTURE_WEIGHTS,
    BeatInput,
    ReelPlan,
    Segment,
    Shot,
    TakeInput,
    check_plan,
    genre_family,
    genre_hashtags,
    plan_json,
    plan_reel,
    post_text,
)

FPS = 24.0


def take(
    take_id: str = "t1",
    *,
    seconds: float = 15.0,
    cues: tuple[Cue, ...] = (),
    motion_peak: tuple[float, float] | None = None,
    face_peak: tuple[float, float] | None = None,
    still: tuple[float, float] | None = None,
    rows: int = 4,
    named: int = 1,
    luma: float = 0.4,
    measured_faces: bool = True,
) -> TakeInput:
    """A measured take: gentle motion everywhere, a motion peak, a close-up face, a held stretch."""

    samples = tuple(round(i / 8, 4) for i in range(int(seconds * 8)))

    def inside(window: tuple[float, float] | None, t: float) -> bool:
        return bool(window) and window[0] <= t < window[1]  # type: ignore[index]

    motion = tuple(
        0.5
        if inside(motion_peak, t)
        else 0.0005
        if inside(still, t)
        else 0.05 + 0.001 * (i % 3)
        for i, t in enumerate(samples)
    )
    faces = (
        tuple(1.0 if inside(face_peak, t) else 0.0 for t in samples)
        if measured_faces
        else ()
    )
    width = seconds / rows
    shots = tuple(
        Shot(i + 1, round(i * width, 3), round((i + 1) * width, 3), named=named)
        for i in range(rows)
    )
    return TakeInput(
        take_id=take_id, duration=seconds, fps=FPS, shots=shots, cues=cues,
        sample_seconds=samples, motion=motion, contrast=tuple(0.2 for _ in samples),
        luma=tuple(luma for _ in samples), faces=faces,
    )  # fmt: skip


def three_beats(take_id: str = "t1") -> list[BeatInput]:
    return [
        BeatInput(1, take_id, 1, 2, "the setup", lines=("plant line",)),
        BeatInput(2, take_id, 3, 3, "the turn", lines=("pivot line",)),
        BeatInput(3, take_id, 4, 4, "the reveal", payoff=True, lines=("new fact",)),
    ]


#: Rows of 3.75 s: plant 0-7.5, pivot 7.5-11.25, new fact 11.25-15.
CUES = (
    Cue(0.5, 2.5, "plant line"),
    Cue(8.0, 9.5, "pivot line"),
    Cue(12.0, 13.0, "new fact"),
)


# --- 1. the cold open never spoils the ending ---------------------------------------------------


def test_the_cold_open_never_comes_from_the_new_fact_even_when_it_is_the_strongest_picture() -> (
    None
):
    """The reveal moves most and shows the closest face: the reel still opens on the pivot."""

    measured = take(cues=CUES, motion_peak=(12.0, 14.0), face_peak=(12.0, 14.0))
    plan = plan_reel(3, [measured], three_beats())

    cold = plan.segments[0]
    assert cold.role == "cold_open"
    assert plan.strongest is not None and plan.strongest.role == "pivot"
    assert plan.last_beat == ("t1", 11.25, 15.0)
    assert cold.end <= 11.25 + 1e-6, "the cold open stays before the last beat"
    assert not any("own ending" in w for w in plan.warnings)
    body = plan_json(plan, takes={})
    assert body["cold_open"]["beat_role"] == "pivot"
    assert body["cold_open"]["spoils_ending"] is False
    assert body["cold_open"]["face_source"] == "detector"
    assert body["last_beat"] == {"take": "t1", "start_s": 11.25, "end_s": 15.0}


def test_a_hand_edited_cold_open_inside_the_last_beat_is_warned() -> None:
    measured = take(cues=CUES)
    plan = ReelPlan(
        episode=4,
        seconds=12.0,
        segments=[
            Segment("t1", 12.0, 13.5, "cold_open"),
            Segment("t1", 0.0, 7.5, "plant"),
            Segment("t1", 11.25, 13.15, "new_fact"),
        ],
        last_beat=("t1", 11.25, 15.0),
    )
    warnings = check_plan(plan, [measured])
    assert any("inside the last beat" in w and "own ending" in w for w in warnings)


def test_a_cold_open_overlapping_the_last_segment_is_warned_without_beat_times() -> (
    None
):
    """A plan file from before ``last_beat`` was written still gets the check, from its last segment."""

    plan = ReelPlan(
        episode=1,
        seconds=12.0,
        segments=[
            Segment("t1", 12.0, 13.5, "cold_open"),
            Segment("t1", 0.0, 7.5, "plant"),
            Segment("t1", 11.25, 13.15, "new_fact"),
        ],
    )
    assert any("overlaps the ending" in w for w in check_plan(plan, [take(cues=CUES)]))


def test_a_one_beat_episode_gets_no_cold_open_rather_than_its_ending() -> None:
    measured = take(cues=(Cue(2.0, 3.0, "the only line"),), rows=1)
    plan = plan_reel(1, [measured], [BeatInput(1, "t1", 1, 1, lines=("x",))])

    assert [s.role for s in plan.segments] == ["new_fact"]
    assert any("no flash-forward" in w for w in plan.warnings)


def test_any_episode_any_take_the_cold_open_comes_before_the_reveal() -> None:
    """Episode 7, beats over two takes: the new fact is on t2; the cold open never touches it."""

    t1 = take("t1", cues=(Cue(1.0, 2.0, "a"), Cue(6.0, 7.0, "b")))
    t2 = take(
        "t2",
        cues=(Cue(1.0, 2.0, "c"), Cue(8.0, 9.0, "d"), Cue(12.0, 13.0, "e")),
        motion_peak=(11.5, 14.5),
        face_peak=(11.5, 14.5),
    )
    beats = [
        BeatInput(1, "t1", 1, 4, lines=("a",)),
        BeatInput(2, "t2", 1, 2, lines=("c",)),
        BeatInput(3, "t2", 3, 3, lines=("d",)),
        BeatInput(4, "t2", 4, 4, payoff=True, lines=("e",)),
    ]
    plan = plan_reel(7, [t1, t2], beats, seconds=15.0)

    assert plan.episode == 7
    cold = plan.segments[0]
    assert cold.role == "cold_open"
    assert not (cold.take == "t2" and cold.end > 11.25 + 1e-6)
    assert plan.ends_on_new_fact()


# --- 2. faces and stillness, weighted by genre --------------------------------------------------


@pytest.mark.parametrize(
    ("genre", "family"),
    [
        ("action", "action"),
        ("war_god_return", "action"),
        ("urban_romance", "intimate"),
        ("bl", "intimate"),
        ("slice_of_life", "intimate"),
        ("cozy", "intimate"),
        ("horror", "horror"),
        ("scp_horror", "horror"),
        ("mystery", "default"),
        ("", "default"),
        ("blood_feud", "default"),
    ],
)
def test_genre_family(genre: str, family: str) -> None:
    assert genre_family(genre) == family


def test_every_family_weighs_every_signal() -> None:
    """Weights, not rules: no genre switches a signal off, and each row sums to 1."""

    for family, weights in PICTURE_WEIGHTS.items():
        assert set(weights) == {"motion", "contrast", "face", "stillness"}, family
        assert all(w > 0 for w in weights.values()), family
        assert sum(weights.values()) == pytest.approx(1.0), family


#: Plant row 1 (0-3.75 s), pivot rows 2-3 (3.75-11.25 s), new fact row 4.
PIVOT_CUES = (
    Cue(0.5, 2.5, "plant line"),
    Cue(4.0, 4.4, "pivot line"),
    Cue(12.0, 13.0, "new fact"),
)


def _cold_in_pivot(genre: str) -> float:
    """The pivot holds two moments: a big move at 5-7 s and a held close-up at 8.5-10.5 s."""

    measured = take(
        cues=PIVOT_CUES,
        motion_peak=(5.0, 7.0),
        face_peak=(8.5, 10.5),
        still=(8.5, 10.5),
    )
    beats = [
        BeatInput(1, "t1", 1, 1, lines=("plant line",)),
        BeatInput(2, "t1", 2, 3, lines=("pivot line",)),
        BeatInput(3, "t1", 4, 4, payoff=True, lines=("new fact",)),
    ]
    plan = plan_reel(1, [measured], beats, genre=genre)
    assert plan.strongest is not None and plan.strongest.role == "pivot"
    return plan.strongest.at


def test_romance_opens_on_the_face_and_action_on_the_move() -> None:
    assert 8.5 <= _cold_in_pivot("urban_romance") < 10.5
    assert 8.5 <= _cold_in_pivot("slice_of_life") < 10.5
    assert 5.0 <= _cold_in_pivot("action") < 7.0


def test_horror_opens_on_the_held_close_up() -> None:
    assert 8.5 <= _cold_in_pivot("horror") < 10.5


def test_without_a_detector_the_head_count_stands_in_for_a_face() -> None:
    measured = take(cues=CUES, measured_faces=False, named=1)
    plan = plan_reel(1, [measured], three_beats())
    assert plan.strongest is not None
    assert plan.strongest.face_source == "head_count"
    assert plan.strongest.parts["face"] == pytest.approx(HEAD_COUNT_FACE)


def test_closeup_score_grows_with_the_face() -> None:
    assert closeup_score(0.0) == 0.0
    assert closeup_score(0.015) == pytest.approx(0.5)
    assert closeup_score(0.2) == 1.0
    assert FaceReading(0, 0.3).score == 0.0


# --- 3. openings: dark, static, faceless -------------------------------------------------------


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


def test_the_reels_first_second_is_checked_from_the_plan() -> None:
    dark = take(cues=CUES, luma=DARK_LUMA / 3)
    plan = plan_reel(2, [dark], three_beats())
    assert plan.opening is not None
    assert plan.opening["frame0_luma"] < DARK_LUMA
    assert any("the reel's first second opens dark" in w for w in plan.warnings)
    assert "warnings" in plan_json(plan, takes={})["opening"]


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


# --- 4. endings on the peak --------------------------------------------------------------------


def test_the_new_fact_ends_on_its_last_line_not_a_settled_tail() -> None:
    measured = take(cues=CUES, still=(13.0, 15.0))
    plan = plan_reel(2, [measured], three_beats())

    last = plan.segments[-1]
    assert last.role == "new_fact"
    assert last.end <= 13.0 + TAIL_MAX_SECONDS
    assert plan.tail is not None and plan.tail["settled_tail_s"] <= TAIL_MAX_SECONDS
    assert not any("past its last line" in w for w in plan.warnings)


def test_a_settled_tail_on_a_hand_plan_is_warned() -> None:
    measured = take(cues=CUES, still=(13.0, 15.0))
    plan = ReelPlan(
        episode=2,
        seconds=12.0,
        segments=[
            Segment("t1", 8.0, 9.6, "cold_open"),
            Segment("t1", 0.0, 3.0, "plant"),
            Segment("t1", 11.25, 15.0, "new_fact"),
        ],
    )
    warnings = check_plan(plan, [measured])
    assert any("past its last line or action" in w for w in warnings)


def test_tail_reading_counts_motion_as_action() -> None:
    times = [i / 8 for i in range(40)]
    moving = [0.02 if t < 4.5 else 0.0 for t in times]
    reading = tail_reading(times, moving, end=5.0, last_mark=2.0)
    assert reading.settled_seconds == pytest.approx(5.0 - 4.375)
    assert tail_warning(reading) is not None
    busy = tail_reading(times, [0.02] * 40, end=5.0, last_mark=2.0)
    assert tail_warning(busy) is None
    assert STATIC_MOTION < 0.02


def test_the_optional_ending_is_recorded_and_hard_is_the_default() -> None:
    measured = take(cues=CUES)
    assert plan_reel(2, [measured], three_beats()).ending == "hard"
    plan = plan_reel(2, [measured], three_beats(), ending="freeze-black")
    body = plan_json(plan, takes={})
    assert body["ending"] == {"style": "freeze-black", "freeze_s": 0.4, "black_s": 0.3}
    with pytest.raises(ValueError, match="freeze-black"):
        plan_reel(2, [measured], three_beats(), ending="fade")


# --- 5. one hashtag per genre -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("genre", "tags"),
    [
        ("slice_of_life", ["sliceoflife"]),
        ("war_god_return", ["wargodreturn"]),
        ("Slice of Life", ["sliceoflife"]),
        ("urban_romance, horror", ["urbanromance", "horror"]),
        ("bl / cozy / mystery", ["bl", "cozy"]),
        ("horror", ["horror"]),
        ("", []),
    ],
)
def test_genre_hashtags_are_one_word_per_genre(genre: str, tags: list[str]) -> None:
    assert genre_hashtags(genre) == tags


def test_post_text_never_splits_a_genre_into_words() -> None:
    text = post_text(series="Tea House", episode=3, genre="slice_of_life")
    assert "#sliceoflife #shortdrama" in text
    assert "#slice" not in text.replace("#sliceoflife", "") and "#of" not in text


def test_plan_json_is_serialisable_with_every_new_field() -> None:
    plan = plan_reel(5, [take(cues=CUES)], three_beats(), genre="horror")
    body = plan_json(plan, takes={})
    for key in ("cold_open", "last_beat", "opening", "tail", "ending"):
        assert key in body
    assert body["cold_open"]["genre_family"] == "horror"
    assert body["cold_open"]["weights"] == PICTURE_WEIGHTS["horror"]
    json.dumps(body)
