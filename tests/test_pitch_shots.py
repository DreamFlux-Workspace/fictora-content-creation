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
    assert "1. Extreme close-up [opens]:" in text
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
