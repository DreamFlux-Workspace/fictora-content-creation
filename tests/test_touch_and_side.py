"""Free warnings before a paid draw: a touch with no owner, a one-sided feature with no side.

Three Payments Late: on 10-06 a gripping hand attached to no one came in from
behind Noor (t1 row 2, a $0.30 redraw); on 10-05 Noor's single chrome forearm
was drawn on both arms (a $0.30 plate redraw). Both are warnings, never stops:
the server writes the owner line and the bans itself when it draws.
"""

from __future__ import annotations

import io
from pathlib import Path

from creation import episode_commands as ec
from creation.spine_view import shot_list_lines
from creation.touch_and_side import (
    one_sided_side_lines,
    touch_owner_heads_up,
    unowned_touches,
)
from fake_api import FakeApi, spine_fixture
from test_redraw_board_note import REGEN, _record_digest, _regen_routes

CLAMP = "clamps Hana's wrist across the counter"


def _ren_grabs_hana(
    spine: dict, *, interaction: str = CLAMP, position: str = "behind the counter"
) -> dict:
    """Row 1 of episode 1 stages Hana and Ren; Ren takes her wrist."""

    frame = next(f for f in spine["frames"] if f["frame_id"] == "frame_episode_01_01")
    frame["visual_brief"]["subject_blocking"] = [
        {
            "cast_id": "cast_hana",
            "frame_position": "frame left",
            "pose": "steady",
            "interaction": "pulls away",
        },
        {
            "cast_id": "cast_ren",
            "frame_position": position,
            "pose": "leans in",
            "interaction": interaction,
        },
    ]
    return frame


def test_a_touch_with_no_hand_or_side_is_named_with_the_edit() -> None:
    spine = spine_fixture()
    _ren_grabs_hana(spine)

    text = touch_owner_heads_up(spine, episode=1, desk="/d") or ""

    assert text.startswith("!! Before the boards (warning only, nothing stopped):")
    assert (
        f'ep01 t1 frame_episode_01_01 (row 1): Ren "{CLAMP}" does not say which hand or which side of the '
        "frame it comes from" in text
    )
    assert (
        "fictora-produce edit --desk /d --episode 1 --frame frame_episode_01_01 --set subject_blocking.1.interaction="
        in text
    )
    # Every board's shot list carries the same line, to read the drawn board by.
    assert any(
        "!! row 1: Ren" in line and "attached to no one" in line
        for line in shot_list_lines(spine, episode=1)
    )


def test_a_touch_that_says_whose_hand_and_from_where_is_clean() -> None:
    spine = spine_fixture()
    _ren_grabs_hana(
        spine,
        interaction="his right hand clamps Hana's wrist",
        position="frame right across the counter",
    )
    assert touch_owner_heads_up(spine, episode=1, desk="/d") is None
    # Joined to his shoulder says where it comes from too.
    _ren_grabs_hana(
        spine,
        interaction="his right hand clamps Hana's wrist, his arm joined to his shoulder",
    )
    assert touch_owner_heads_up(spine, episode=1, desk="/d") is None
    # Only the hand side missing.
    _ren_grabs_hana(spine, position="frame right")
    hits = unowned_touches(
        [next(f for f in spine["frames"] if f["frame_id"] == "frame_episode_01_01")],
        set_index=1,
        cast_names={"cast_hana": "Hana", "cast_ren": "Ren"},
    )
    assert [hit.missing for hit in hits] == [("which hand",)]


def test_what_is_not_a_touch_and_frames_with_one_person() -> None:
    spine = spine_fixture()
    for words in (
        "holds still behind the counter",
        "stands close without touching Hana",
        "holds her gaze",
    ):
        _ren_grabs_hana(spine, interaction=words)
        assert touch_owner_heads_up(spine, episode=1, desk="/d") is None, words
    # The fixture's own frames stage one person each.
    assert touch_owner_heads_up(spine_fixture(), episode=1, desk="/d") is None


def test_a_redraw_prints_the_warning_and_still_redraws(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _ren_grabs_hana(api.spine_doc)
    out = io.StringIO()

    ec.run_redraw_board(desk, episode=1, take_id="t1", cause="ren row", out=out)

    assert "Before the boards (warning only, nothing stopped)" in out.getvalue()
    assert api.posted(REGEN), "a warning never stops the redraw"


def _card(spine: dict, *, silhouette: str, wardrobe: list[str] | None = None) -> dict:
    card = spine["cast"][0]
    card["visual_brief"] = {
        "face_anchors": ["oval face"],
        "hair_anchors": ["short bleached hair"],
        "silhouette": silhouette,
        "wardrobe_anchors": wardrobe or ["apron"],
    }
    return card


def test_a_one_sided_feature_with_no_side_is_named() -> None:
    spine = spine_fixture()
    _card(
        spine,
        silhouette="small frame with a slim chrome forearm",
        wardrobe=["black eyepatch"],
    )
    lines = one_sided_side_lines(spine)
    assert lines == [
        '!! Hana: "black eyepatch" names no side. Write the character\'s own side on the card before the plate '
        '("her own left forearm"), or it can be drawn on the wrong side or on both (warning only).',
        '!! Hana: "small frame with a slim chrome forearm" names no side. Write the character\'s own side on the '
        'card before the plate ("her own left forearm"), or it can be drawn on the wrong side or on both (warning only).',
    ]


def test_a_one_sided_feature_with_its_side_or_on_both_is_clean() -> None:
    spine = spine_fixture()
    _card(
        spine,
        silhouette="slim chrome left forearm; ordinary right arm",
        wardrobe=["two chrome arms", "apron"],
    )
    assert one_sided_side_lines(spine) == []
    assert one_sided_side_lines(spine_fixture()) == []


def test_the_script_yes_carries_the_touch_warning(desk: Path, api: FakeApi) -> None:
    from conftest import set_phase
    from creation import orchestrate

    set_phase(desk, "wait_script")
    _ren_grabs_hana(api.spine_doc)

    scripted = orchestrate.approve_gate(desk, gate="script")

    assert "script approved on the API" in scripted.message
    assert (
        'Ren "clamps Hana\'s wrist across the counter" does not say which hand'
        in scripted.message
    )
