"""The kit reads an episode's shots the way the app's pitch card does (fictora-drama, 9 Oct 2026).

``tests/data/pitch_card_parity.json`` is a copy of the server's
``tests/drama_generation/fixtures/pitch_card_parity.json``: the same story slice
must give the same card, digest included, on both sides. A change to the reading
lands in the server first; regenerate the fixture there and copy it here.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from creation.pitch_card import RESTRAINT_KINDS, run_pitch
from creation.pitch_shots import (
    EXPRESSION_DIRECTIONS,
    camera_tag,
    drop_lock_tokens,
    pitch_digest,
    pitch_takes,
    shot_lines,
)
from test_pitch_card import card

_PARITY = Path(__file__).parent / "data" / "pitch_card_parity.json"
_CODE = re.compile(r"\b[a-z]+_[a-z_]+\b")


def _case() -> dict[str, Any]:
    return json.loads(_PARITY.read_text(encoding="utf-8"))


def _spine(case: dict[str, Any]) -> dict[str, Any]:
    # What GET /v1/spines/{id} sends: each card with the server's short_name.
    names = case.get("short_names") or {}
    cast = [
        {**card, "short_name": names[card["cast_id"]]}
        if card["cast_id"] in names
        else card
        for card in case["cast"]
    ]
    return {
        "cast": cast,
        "frames": case["frames"],
        "beats": case["beats"],
        "beats_per_storyboard_set": case["beats_per_storyboard_set"],
        "episode_summaries": [{"episode_id": case["episode_id"], "ordinal": 1}],
    }


def test_the_kit_reads_the_same_card_as_the_server() -> None:
    case = _case()

    takes = pitch_takes(_spine(case), case["episode_id"])

    assert takes == case["expected"]["takes"]
    assert pitch_digest(takes, []) == case["expected"]["pitch_digest"]


def test_no_code_reaches_the_shots() -> None:
    case = _case()
    text = "\n".join(shot_lines(pitch_takes(_spine(case), case["episode_id"])))

    assert not _CODE.search(text), text
    assert "Expression:" not in text and "Row " not in text


def test_every_restrained_code_the_kit_knows_reads_as_a_visible_face() -> None:
    for kind in RESTRAINT_KINDS & set(EXPRESSION_DIRECTIONS):
        assert "blank" not in EXPRESSION_DIRECTIONS[kind], kind


def test_camera_tags_match_the_server() -> None:
    assert camera_tag("close-up", "low angle") == "Close-up · low angle"
    assert camera_tag("insert", "top-down") == "Insert · from above"
    assert camera_tag(None) == "Medium"


def test_pitch_prints_the_episode_shots_from_the_desk_story(desk: Path) -> None:
    case = _case()
    spine = _spine(case)
    spine["episode_summaries"] = [{"episode_id": case["episode_id"], "ordinal": 1}]
    (desk / "api").mkdir(exist_ok=True)
    (desk / "api" / "spine.json").write_text(json.dumps(spine), encoding="utf-8")
    path = desk / "pitch.json"
    path.write_text(json.dumps(card()), encoding="utf-8")
    out = io.StringIO()

    assert run_pitch(desk, episode=1, file=path, out=out) == 0

    text = out.getvalue()
    assert "Shots, by take (6; what an app creator approves):" in text
    assert "1. Extreme close-up · Locked off [opens]:" in text
    assert '"I don\'t sell to my rival!"' in text
    assert "(Mitsu stares, stunned)" in text


def test_pitch_without_a_desk_story_prints_no_shots(desk: Path) -> None:
    path = desk / "pitch.json"
    path.write_text(json.dumps(card()), encoding="utf-8")
    out = io.StringIO()

    run_pitch(desk, episode=1, file=path, out=out)

    assert "Shots, by take" not in out.getvalue()


def test_without_the_servers_short_name_the_full_name_is_shown() -> None:
    case = _case()
    spine = _spine(case)
    for entry in spine["cast"]:
        entry.pop("short_name", None)

    shots = [
        shot
        for take in pitch_takes(spine, case["episode_id"])
        for shot in take["shots"]
    ]

    assert shots[0]["line"]["speaker"] == "Mitsu Hanakaze"


# L-20261001-31 follow-up: the shot list shows the camera the take plays, never
# a speaking beat's lip-sync "Locked" token.


def _row_spine(*, move: str, beat_camera: str = "locked") -> dict[str, Any]:
    """One speaking beat on a two-row board; its rows' own plan moves ``move``."""

    frames = [
        {
            "frame_id": f"f{ordinal}",
            "episode_id": "episode_01",
            "ordinal": ordinal,
            "board_row": (ordinal + 1) // 2,
            "visual_brief": {"row_direction": {"camera_move": move}}
            if ordinal % 2
            else {},
        }
        for ordinal in (1, 2, 3, 4)
    ]
    beat = {
        "beat_id": "b1",
        "episode_id": "episode_01",
        "ordinal": 1,
        "frame_id": "f1",
        "motion_intent": "SHOT 1 - medium, eye level, Locked. Mara grips the switch. "
        "SHOT 2 - close-up, low angle, Locked. Her thumb hovers.",
        "motion_direction": {"camera": {"move": beat_camera}},
        "dialogue_lines": [{"cast_id": "c1", "text": "Don't."}],
    }
    return {
        "storyboard_board_kind": "row",
        "cast": [{"cast_id": "c1", "name": "Mara"}],
        "frames": frames,
        "beats": [beat],
        "beats_per_storyboard_set": [1],
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
    }


def test_a_speaking_shot_shows_its_rows_move_not_the_lip_sync_lock() -> None:
    shots = pitch_takes(_row_spine(move="dolly_in"), "episode_01")[0]["shots"]

    assert [shot["camera_move"] for shot in shots] == ["Push in", "Push in"]
    assert not any("Locked" in shot["scene"] for shot in shots)
    assert "Medium · eye level · Push in [opens]" in "\n".join(
        shot_lines([{"take": 1, "shots": shots}])
    )


def test_a_speaking_row_whose_move_the_take_drops_shows_no_move() -> None:
    shots = pitch_takes(_row_spine(move="orbit_left"), "episode_01")[0]["shots"]

    assert shots[0]["camera_move"] is None
    assert shots[1]["camera_move"] == "Orbit left"


def test_the_move_never_changes_the_digest() -> None:
    takes = pitch_takes(_row_spine(move="dolly_in"), "episode_01")
    bare = [
        {**take, "shots": [{**shot, "camera_move": None} for shot in take["shots"]]}
        for take in takes
    ]

    assert pitch_digest(takes, []) == pitch_digest(bare, [])


def test_a_story_lock_stays_in_the_scene() -> None:
    assert drop_lock_tokens("The door stays locked.") == "The door stays locked."
    assert drop_lock_tokens("Low angle, locked off, her thumb hovers.") == (
        "Low angle, her thumb hovers."
    )
