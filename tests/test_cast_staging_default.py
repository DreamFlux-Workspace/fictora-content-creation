"""``edit --frame N --set cast_refs`` that adds someone stages them by default (#19).

The server refuses someone added to a shot with no staging
(``frame_cast_needs_staging``), so the operator had to write their whole
``subject_blocking`` entry by hand. The kit now writes a default one (their
side on the episode's other frames, else opposite the lead; still pose; facing
the lead) and sends it with the whole brief, only for someone already in the
episode (a line in it, or a shot of it): anyone else is refused before
anything is sent (founder, 13 Oct 2026).
"""

from __future__ import annotations

import pytest

from creation import episode_commands as ec
from creation.default_staging import (
    ADDED_CAST_POSE,
    default_subject_blocking,
    frame_side,
)
from fake_api import spine_fixture


def test_adding_someone_by_cast_refs_sends_a_default_staging_for_them() -> None:
    spine = spine_fixture()
    before = dict(spine["frames"][0]["visual_brief"])

    patch, changed = ec.build_patch(
        spine,
        episode=1,
        frame="frame_episode_01_01",
        assignments=[ec.parse_assignment('cast_refs=["Hana", "Ren"]')],
    )

    (entry,) = patch["frames"]
    assert entry["cast_refs"] == ["cast_hana", "cast_ren"]
    blocking = entry["visual_brief"]["subject_blocking"]
    assert blocking[0] == before["subject_blocking"][0]
    assert blocking[1]["cast_id"] == "cast_ren"
    assert blocking[1]["pose"] == ADDED_CAST_POSE
    assert blocking[1]["gaze"] == "looking toward Hana"
    # Everything else in the brief is sent as it was.
    assert {
        k: v for k, v in entry["visual_brief"].items() if k != "subject_blocking"
    } == {k: v for k, v in before.items() if k != "subject_blocking"}
    assert any(
        "Ren stands at" in line and "default staging" in line for line in changed
    )
    assert "  cast_refs: Hana  ->  Hana, Ren" in changed


def test_the_added_person_stands_opposite_a_lead_on_one_side() -> None:
    spine = spine_fixture()
    frame = spine["frames"][1]  # Hana at "bottom edge, left"

    added = default_subject_blocking(spine, frame, ["cast_hana", "cast_ren"])[1]

    assert frame_side(added["frame_position"]) == "right"


def test_the_added_person_keeps_the_side_their_other_frames_give_them() -> None:
    spine = spine_fixture()
    spine["frames"][1]["visual_brief"]["subject_blocking"].append(
        {
            "cast_id": "cast_ren",
            "frame_position": "frame left, by the door",
            "pose": "p",
            "gaze": "g",
            "interaction": "i",
        }
    )

    added = default_subject_blocking(
        spine, spine["frames"][0], ["cast_hana", "cast_ren"]
    )[1]

    assert frame_side(added["frame_position"]) == "left"


def test_frame_side_ignores_hands_and_relative_places() -> None:
    assert frame_side("frame right, in the middle plane") == "right"
    assert frame_side("centre, her left hand on the rail") == "center"
    assert frame_side("just right of the door") is None


def test_someone_not_in_the_episode_is_refused_before_anything_is_sent() -> None:
    spine = spine_fixture()
    spine["cast"].append({**spine["cast"][1], "cast_id": "cast_kai", "name": "Kai"})

    with pytest.raises(ec.CommandStopped, match="Kai is not in this episode") as caught:
        ec.build_patch(
            spine,
            episode=1,
            frame="frame_episode_01_01",
            assignments=[ec.parse_assignment('cast_refs=["Hana", "Kai"]')],
        )

    assert "line --add" in str(caught.value)
    assert "Nothing was sent" in str(caught.value)
