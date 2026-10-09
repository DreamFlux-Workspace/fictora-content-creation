"""Kit pre-board / plate warnings that fired on nothing (9 Oct batch, Stream D).

- Gallery L-20261008-18: "lamplight holds" and "holding his profile" read as a hand hold.
- Gallery L-20261008-30: every age flagged "written in words" though all were digits.
- Noodle24 L-20261007-4: "Mr. Tanaka is 24" read from "Noodle 24" in his role; his card says late 80s.
"""

from __future__ import annotations

import pytest

from creation.harness_rules import (
    adult_face_lines,
    staging_contradiction_lines,
    word_age_lines,
)


def _frame_spine(staging: str, forbidden: list[str]) -> dict:
    return {
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
        "beats": [],
        "frames": [
            {
                "frame_id": "f1",
                "episode_id": "episode_01",
                "ordinal": 1,
                "visual_brief": {"staging": staging, "forbidden_elements": forbidden},
            }
        ],
        "cast": [],
    }


@pytest.mark.parametrize(
    "staging",
    [
        "Warm lamplight holds on her face as she reads the letter.",
        "The camera holds, holding his profile against the window.",
        "She holds his gaze across the gallery.",
        "The shot holds for a beat on the empty frame.",
    ],
)
def test_a_camera_light_or_look_hold_is_not_a_hand_hold(staging: str) -> None:
    spine = _frame_spine(staging, ["she is never holding anything"])
    assert staging_contradiction_lines(spine, episode=1) == []


def test_a_real_hand_hold_that_is_also_forbidden_is_still_named() -> None:
    spine = _frame_spine(
        "She holds his hand under the table.", ["she is never holding anything"]
    )
    assert staging_contradiction_lines(spine, episode=1) != []


def _cast_spine(**card: object) -> dict:
    return {
        "title": "Noodle24",
        "cast": [{"cast_id": "c1", "name": "Mr. Tanaka", **card}],
        "beats": [],
    }


def test_a_number_word_in_the_backstory_is_not_an_age_in_words() -> None:
    spine = _cast_spine(
        role="Owner of the gallery for twenty years; thirty paintings, fifteen sold.",
        backstory="Lost forty, then fifty, at the auction.",
        visual_brief={"age_band": "52", "description": "grey temples, narrow face"},
    )
    assert word_age_lines(spine) == []


def test_an_age_in_words_where_the_card_states_its_age_is_still_warned() -> None:
    assert word_age_lines(_cast_spine(visual_brief={"age_band": "late twenties"}))
    # No age field: the look the plate is drawn from is read.
    assert word_age_lines(
        _cast_spine(visual_brief={"description": "twenty-six, adult woman"})
    )


def test_the_number_in_the_show_title_is_never_read_as_an_age() -> None:
    spine = _cast_spine(
        role="Regular at Noodle 24 since it opened; eats alone.",
        visual_brief={"look": "frail"},
    )
    assert not any("is 24" in line for line in adult_face_lines(spine)), (
        adult_face_lines(spine)
    )


def test_a_decade_age_is_read() -> None:
    spine = _cast_spine(
        role="Regular at Noodle 24.", visual_brief={"age_band": "late 80s"}
    )
    lines = adult_face_lines(spine)
    assert lines and lines[0].startswith("Mr. Tanaka is 88"), lines


def test_a_number_inside_a_place_name_is_not_an_age_but_a_stated_one_is() -> None:
    spine = {
        "cast": [
            {
                "cast_id": "c1",
                "name": "Ren",
                "role": "Ren, 31, works nights at Studio 54.",
            }
        ],
        "beats": [],
    }
    lines = adult_face_lines(spine)
    assert lines and lines[0].startswith("Ren is 31"), lines
    spine["cast"][0]["role"] = "Works nights at Studio 54."
    assert adult_face_lines(spine) == []
    spine["cast"][0]["role"] = "Age 34, works nights."
    assert adult_face_lines(spine)[0].startswith("Ren is 34")
