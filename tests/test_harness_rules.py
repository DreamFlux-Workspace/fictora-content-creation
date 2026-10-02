"""Episode rules the harness checks before a take, a plate, a board, a cue, or a join."""

from __future__ import annotations

from creation.harness_rules import (
    creature_sound_stop,
    film_stops,
    gain_match_note,
    hook_mouth_stop,
    late_opening_line,
    locked_camera_line,
    near_touch_line,
    short_line_hold,
    staging_contradiction_lines,
    thin_take_lines,
    seam_fix_line,
    thought_short_of_cut,
    thumbnail_answered_audio_error,
    young_creature_lines,
)


def _spine() -> dict:
    return {
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
        "beats_per_storyboard_set": [3],
        "cast": [
            {"cast_id": "cast_hana", "name": "Hana"},
            {"cast_id": "cast_sota", "name": "Sota"},
        ],
        "beats": [
            {
                "beat_id": "b1",
                "episode_id": "episode_01",
                "ordinal": 1,
                "motion_intent": "Hana counts the cabins",
                "motion_direction": {"subject_cast_id": "cast_hana"},
                "dialogue_lines": [
                    {"line_id": "l1", "cast_id": "cast_hana", "text": "Thirty-three."}
                ],
            },
            {
                "beat_id": "b2",
                "episode_id": "episode_01",
                "ordinal": 2,
                "motion_intent": "Sota leans in",
                "motion_direction": {"subject_cast_id": "cast_sota"},
                "shot_plan": [{"size": "close-up", "subject": "Sota"}],
                "dialogue_lines": [
                    {"line_id": "l2", "cast_id": "cast_sota", "text": "Scoot in."}
                ],
            },
            {
                "beat_id": "b3",
                "episode_id": "episode_01",
                "ordinal": 3,
                "motion_intent": "the seat sinks",
                "motion_direction": {"subject_cast_id": "cast_hana"},
                "shot_plan": [
                    {"size": "close-up", "subject": "the cushion"},
                    {"size": "wide", "subject": "the wheel"},
                ],
                "dialogue_lines": [],
            },
        ],
    }


def test_a_clean_episode_can_be_filmed() -> None:
    assert film_stops(_spine(), episode=1) == []


def test_inner_voice_words_and_a_split_speaker_and_an_early_extra_shot_stop_the_film() -> (
    None
):
    spine = _spine()
    spine["beats"][0]["motion_intent"] = "Hana (inner voice) counts"
    spine["beats"][1]["dialogue_lines"][0]["cast_id"] = "cast_hana"
    spine["beats"][1]["shot_plan"] = [
        {"size": "close-up", "subject": "Sota"},
        {"size": "wide", "subject": "the wheel"},
    ]

    stops = film_stops(spine, episode=1)

    assert any("inner voice" in line and "beat 1" in line for line in stops)
    assert any(
        "motion subject must match" in line and "beat 2" in line for line in stops
    )
    assert any("last beat" in line and "beat 2" in line for line in stops)
    assert not any("beat 3" in line for line in stops)


def test_an_off_screen_line_may_play_over_someone_else() -> None:
    spine = _spine()
    spine["beats"][0]["dialogue_lines"][0]["off_screen"] = True
    spine["beats"][0]["dialogue_lines"][0]["cast_id"] = "cast_sota"

    assert film_stops(spine, episode=1) == []


def test_gain_matching_a_levelled_pair_is_named() -> None:
    assert gain_match_note([0.2, -0.2]) is None
    note = gain_match_note([-2.4, 2.4])
    assert note is not None
    assert "--no-gain-match" in note
    assert "-2.4 dB" in note and "+2.4 dB" in note


def test_a_thought_that_ends_before_the_cut_is_named() -> None:
    cues = [{"start": 6.0, "seconds": 1.2}]

    assert (
        thought_short_of_cut(cues, duration=15.0, label="ep01 t1", last_take=True)
        is None
    )
    note = thought_short_of_cut(cues, duration=15.0, label="ep01 t1", last_take=False)
    assert note is not None and "7.8s before the cut" in note

    running_to_the_cut = [{"start": 8.75, "seconds": 6.0}]
    assert (
        thought_short_of_cut(
            running_to_the_cut, duration=15.0, label="ep01 t1", last_take=False
        )
        is None
    )


def test_a_loud_seam_is_told_to_add_a_sound_on_the_quiet_side() -> None:
    assert "quiet side" in seam_fix_line() and "dead air" in seam_fix_line()


def test_take_one_flags_a_first_line_that_starts_late() -> None:
    late = late_opening_line(
        ["check-lines: ep01 t1: line 1 ('The egg cracks', Kael) (2.0–3.1 s)"],
        take_id="t1",
    )
    assert late is not None and "2.00s" in late and "trim the head" in late
    assert (
        late_opening_line(
            ["check-lines: ep01 t1: line 1 ('The egg cracks', Kael) (0.4–1.2 s)"],
            take_id="t1",
        )
        is None
    )
    assert (
        late_opening_line(
            ["check-lines: ep01 t2: line 1 ('Run', Kael) (2.0–3.0 s)"],
            take_id="t2",
        )
        is None
    )


def test_a_locked_draft_is_named_and_a_move_clears_it() -> None:
    spine = _spine()
    note = locked_camera_line(spine, episode=1)
    assert note is not None and "edit --beat N --shot" in note
    spine["beats"][0]["shot_plan"] = [{"camera": "dolly"}]
    assert locked_camera_line(spine, episode=1) is None


def test_a_hand_over_the_hook_stops_and_a_later_gasp_does_not() -> None:
    spine = _spine()
    assert hook_mouth_stop(spine, episode=1) is None
    spine["beats"][0]["reaction_kind"] = "dramatic_gasp"
    stopped = hook_mouth_stop(spine, episode=1)
    assert stopped is not None and "forbidden elements" in stopped
    spine["beats"][0]["reaction_kind"] = None
    spine["beats"][1]["motion_intent"] = "Sota puts a hand over his mouth"
    assert hook_mouth_stop(spine, episode=1) is None


def test_a_young_creature_without_proportions_is_named() -> None:
    spine = _spine()
    assert young_creature_lines(spine) == []
    spine["cast"].append(
        {"cast_id": "cast_cinder", "name": "Cinder", "visual_brief": "a newborn dragon"}
    )
    lines = young_creature_lines(spine)
    assert len(lines) == 1 and "oversized head" in lines[0]
    spine["cast"][-1]["visual_brief"] = (
        "a newborn, oversized head, large eyes, small body"
    )
    assert young_creature_lines(spine) == []


def test_a_creature_cue_must_say_it_is_not_a_cat() -> None:
    stopped = creature_sound_stop("a monster as the door opens")
    assert stopped is not None and "names the sound" in stopped
    assert creature_sound_stop("a wolf howl at the gate") is None
    assert creature_sound_stop("a cat meow") is None


def test_a_short_line_held_for_seconds_is_named() -> None:
    assert short_line_hold("A fox.", start=2.8, end=10.4) is not None
    assert "line-start" in (short_line_hold("여우", start=8.6, end=16.2) or "")
    assert short_line_hold("여우", start=8.6, end=10.4) is None
    assert short_line_hold("Holding my hand now?", start=1.0, end=8.0) is None


def test_a_near_touch_and_a_hold_that_is_also_forbidden_are_named() -> None:
    spine = _spine()
    assert near_touch_line(spine, episode=1) is None
    spine["beats"][0]["motion_intent"] = "his hand stops an inch away"
    assert near_touch_line(spine, episode=1) is not None
    spine["beats"][0]["motion_intent"] = "his hand stops a hand's width above the back"
    assert near_touch_line(spine, episode=1) is None
    spine["frames"] = [
        {
            "frame_id": "frame_1",
            "episode_id": "episode_01",
            "visual_brief": {
                "subject_blocking": "catches the lamp stem with both hands",
                "forbidden_elements": ["anyone holding the lamp"],
            },
        }
    ]
    lines = staging_contradiction_lines(spine, episode=1)
    assert len(lines) == 1 and "redraw with no note" in lines[0]


def test_a_take_with_fewer_than_three_lines_is_named() -> None:
    spine = _spine()
    lines = thin_take_lines(spine, episode=1, take_count=1)
    assert len(lines) == 1 and "2 spoken" in lines[0]
    spine["beats"][2]["dialogue_lines"] = [
        {"line_id": "l3", "cast_id": "cast_hana", "text": "Look."}
    ]
    assert thin_take_lines(spine, episode=1, take_count=1) == []


def test_a_thumbnail_audio_error_is_recognized() -> None:
    assert thumbnail_answered_audio_error(
        "HTTP 502 operator_audio_failed: The audio could not be made"
    )
    assert not thumbnail_answered_audio_error("HTTP 404 thumbnail route")
