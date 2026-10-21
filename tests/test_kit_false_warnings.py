"""Kit pre-board / plate warnings that fired on nothing (9 Oct batch, Stream D).

- Gallery L-20261008-18: "lamplight holds" and "holding his profile" read as a hand hold.
- Noodle24 L-20261007-4: "Mr. Tanaka is 24" read from "Noodle 24" in his role; his card says late 80s.
"""

from __future__ import annotations

import pytest

from creation.harness_rules import (
    adult_face_lines,
    staging_contradiction_lines,
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


# --- Iron Saint ep 2 frame 8 (frame_episode_02_08_v2, 16 Oct 2026): it blocked the Low Tide film --------------

FRAME_8_STAGING = (
    "Ren catches himself on one knee as mud splashes around the hook blade embedded several paces "
    "beyond his shoulder. Ren's iron right arm stays joined to his right shoulder while his left sandal "
    "and right knee carve the mud; no hand touches his face"
)
FRAME_8_FORBIDDEN = [
    "no kissing",
    "no embracing",
    "no face-touching",
    "no second weapon strike",
    "no hook blade touching Ren's body",
    "no grappling",
]


def test_iron_saint_frame_8_stages_no_hold_and_forbids_none() -> None:
    assert (
        staging_contradiction_lines(
            _frame_spine(FRAME_8_STAGING, FRAME_8_FORBIDDEN), episode=1
        )
        == []
    )


@pytest.mark.parametrize(
    "staging",
    [
        "Ren catches himself on one knee in the mud.",
        "She catches herself against the railing.",
        "They hold themselves still as the guard passes.",
    ],
)
def test_catching_oneself_is_not_a_hand_hold(staging: str) -> None:
    assert (
        staging_contradiction_lines(
            _frame_spine(staging, ["she is never holding anything"]), episode=1
        )
        == []
    )


@pytest.mark.parametrize(
    "forbidden",
    [
        "no hook blade touching Ren's body",
        "no spear tip touching her skin",
        "no blade touches his throat",
    ],
)
def test_an_object_touching_a_body_is_a_contact_rule_not_a_hold_ban(
    forbidden: str,
) -> None:
    spine = _frame_spine("Toma grips the hook-staff with both hands.", [forbidden])
    assert staging_contradiction_lines(spine, episode=1) == []


@pytest.mark.parametrize(
    "forbidden",
    [
        "no holding the staff",
        "no one touching the staff",
        "no hand touching the staff",
        "never let anyone touching the staff",
        "Toma is not gripping anything",
    ],
)
def test_a_real_hold_that_is_forbidden_still_fires(forbidden: str) -> None:
    spine = _frame_spine("Toma grips the hook-staff with both hands.", [forbidden])
    assert staging_contradiction_lines(spine, episode=1) != []
