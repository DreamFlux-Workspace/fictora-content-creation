"""`cast --look` adds to a card's bans and description instead of replacing them (L-20261005-13, L-20261006-13).

Last Call and Not Home: `never:` replaced the card's whole `forbidden_elements` (the bans the story was
drafted with were gone), and a description line replaced the card's own. Now `never:` adds (no repeats,
order kept), `never-remove:` takes a ban off by its words, and a description line is added to the card's
description unless `--replace-description` is given.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from cast_story import HANA_BRIEF, _before_gate, _patches, _story, _writes
from creation import cast_commands as cc
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from fake_api import FakeApi


def _hana(bans: list[str] | None = None) -> dict[str, Any]:
    brief = copy.deepcopy(HANA_BRIEF)
    if bans is not None:
        brief["forbidden_elements"] = bans
    return {
        "cast_id": "cast_hana",
        "name": "Hana",
        "visual_description": "A 28-year-old shop owner with a black bob.",
        "visual_brief": brief,
    }


def _bans(look: str, card: dict[str, Any] | None = None) -> list[str]:
    card = card or _hana(["glasses", "no kissing"])
    patch, _ = cc.look_patch({"cast": [card]}, card, look)
    return patch["visual_brief"]["forbidden_elements"]


# --- never: adds -----------------------------------------------------------------------------------------


def test_never_adds_to_the_cards_bans_and_keeps_their_order() -> None:
    assert _bans("never: a hat; earrings") == [
        "glasses",
        "no kissing",
        "a hat",
        "earrings",
    ]


def test_never_does_not_repeat_a_ban_the_card_has_in_any_case() -> None:
    assert _bans("never: Glasses;  no   kissing; a hat; A HAT") == [
        "glasses",
        "no kissing",
        "a hat",
    ]


def test_never_that_adds_nothing_new_is_no_change() -> None:
    card = _hana(["glasses"])

    patch, changed = cc.look_patch({"cast": [card]}, card, "never: GLASSES")

    assert patch["visual_brief"] == card["visual_brief"]
    assert changed == []


def test_a_json_brief_adds_its_bans_too() -> None:
    assert _bans('{"forbidden_elements": ["a hat"]}') == [
        "glasses",
        "no kissing",
        "a hat",
    ]


def test_a_first_look_takes_the_bans_it_names() -> None:
    sam = {
        "cast_id": "cast_sam",
        "name": "Sam",
        "visual_description": "Never shown",
        "visual_brief": None,
    }
    look = (
        "age: 50s\nface: long face\nhair: grey crew cut\nsilhouette: tall\nwardrobe: navy jacket\n"
        "never: a hat\nA night guard."
    )

    patch, _ = cc.look_patch({"cast": [_hana(), sam]}, sam, look)

    assert patch["visual_brief"]["forbidden_elements"] == ["a hat"]


# --- never-remove: takes one off ------------------------------------------------------------------------


def test_never_remove_takes_the_named_ban_off_and_keeps_the_rest() -> None:
    card = _hana(["glasses", "no kissing", "a hat"])

    patch, changed = cc.look_patch({"cast": [card]}, card, "never-remove: Glasses")

    assert patch["visual_brief"]["forbidden_elements"] == ["no kissing", "a hat"]
    assert "    never draw, removed (never-remove): glasses" in changed
    assert "    never draw, kept: no kissing; a hat" in changed


def test_never_and_never_remove_together() -> None:
    assert _bans("never: a hat\nnever-remove: glasses") == ["no kissing", "a hat"]


def test_json_never_remove_takes_a_ban_off() -> None:
    assert _bans('{"never_remove": ["glasses"]}') == ["no kissing"]


def test_json_never_remove_beside_a_nested_brief_takes_a_ban_off() -> None:
    look = '{"visual_brief": {"forbidden_elements": ["a hat"]}, "never_remove": ["glasses"]}'

    assert _bans(look) == ["no kissing", "a hat"]


def test_never_remove_of_a_ban_the_card_does_not_have_stops() -> None:
    with pytest.raises(ec.CommandStopped) as stopped:
        _bans("never-remove: sunglasses")

    text = str(stopped.value)
    assert "sunglasses is not on Hana's never-draw list" in text
    assert "glasses; no kissing" in text and "Nothing was sent" in text


def test_adding_and_removing_the_same_ban_stops() -> None:
    with pytest.raises(ec.CommandStopped) as stopped:
        _bans("never: glasses\nnever-remove: Glasses")

    assert "both adds and removes: glasses" in str(stopped.value)


def test_the_change_names_what_was_added_and_kept() -> None:
    card = _hana(["glasses"])

    _, changed = cc.look_patch({"cast": [card]}, card, "never: a hat")

    assert changed[0].startswith("  visual_brief.forbidden_elements:")
    assert changed[1:] == [
        "    never draw, added: a hat",
        "    never draw, kept: glasses",
    ]
    assert ec.change_items(changed) == [
        "visual_brief.forbidden_elements"
    ]  # one change, not three


# --- the description -------------------------------------------------------------------------------------


def test_a_description_line_is_added_to_the_cards_own() -> None:
    card = _hana()

    patch, changed = cc.look_patch(
        {"cast": [card]}, card, "Her left sleeve is always rolled up."
    )

    assert patch["visual_description"] == (
        "A 28-year-old shop owner with a black bob. Her left sleeve is always rolled up."
    )
    assert "    was: A 28-year-old shop owner with a black bob." in changed
    assert f"    now: {patch['visual_description']}" in changed
    assert any("--replace-description replaces it" in row for row in changed)


def test_a_description_that_carries_the_old_one_is_taken_as_written() -> None:
    card = _hana()
    longer = "A 28-year-old shop owner with a black bob, flour on her apron."

    patch, _ = cc.look_patch({"cast": [card]}, card, longer)

    assert patch["visual_description"] == longer


def test_a_description_the_card_already_says_changes_nothing() -> None:
    card = _hana()

    patch, changed = cc.look_patch(
        {"cast": [card]}, card, "shop owner with a black bob"
    )

    assert patch["visual_description"] == card["visual_description"]
    assert changed == []


def test_replace_description_replaces_it() -> None:
    card = _hana()

    patch, changed = cc.look_patch(
        {"cast": [card]},
        card,
        "A tired baker at closing time.",
        replace_description=True,
    )

    assert patch["visual_description"] == "A tired baker at closing time."
    assert "    (--replace-description: the card's description is replaced)" in changed


def test_replace_description_without_a_description_stops() -> None:
    card = _hana()

    with pytest.raises(ec.CommandStopped) as stopped:
        cc.look_patch({"cast": [card]}, card, "never: a hat", replace_description=True)

    assert "--replace-description needs the new description for Hana" in str(
        stopped.value
    )


def test_a_new_age_and_new_words_both_reach_the_description() -> None:
    card = _hana()
    card["visual_brief"]["age_band"] = "28"

    patch, _ = cc.look_patch({"cast": [card]}, card, "age: 30\nShe wears a wristwatch.")

    assert patch["visual_description"] == (
        "A 30-year-old shop owner with a black bob. She wears a wristwatch."
    )


# --- through the command ---------------------------------------------------------------------------------


def _look(tmp_path: Path, text: str) -> str:
    path = tmp_path / "look.txt"
    path.write_text(text, encoding="utf-8")
    return f"@{path}"


def test_cast_look_sends_the_cards_bans_with_the_new_one(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    out = io.StringIO()

    cc.run_cast_look(desk, name="Hana", look=_look(tmp_path, "never: a hat"), out=out)

    [patch] = _patches(api)
    assert patch["cast"][0]["visual_brief"]["forbidden_elements"] == [
        "glasses",
        "a hat",
    ]
    assert patch["cast"][0]["visual_description"] == "Hana, the shop owner."
    assert "never draw, added: a hat" in out.getvalue()


def test_a_refused_never_remove_sends_nothing(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))

    with pytest.raises(ec.CommandStopped):
        cc.run_cast_look(
            desk,
            name="Hana",
            look=_look(tmp_path, "never-remove: a hat"),
            out=io.StringIO(),
        )

    assert _writes(api) == []


def test_the_replace_description_flag_reaches_the_patch(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    look = _look(tmp_path, "A baker at closing time.")

    assert (
        produce_main(["cast", "--desk", str(desk), "--name", "Hana", "--look", look])
        == 0
    )
    assert (
        produce_main(
            [
                "cast",
                "--desk",
                str(desk),
                "--name",
                "Hana",
                "--look",
                look,
                "--replace-description",
            ]
        )
        == 0
    )

    added, replaced = (p["cast"][0]["visual_description"] for p in _patches(api))
    assert added == "Hana, the shop owner. A baker at closing time."
    assert replaced == "A baker at closing time."


def test_the_preview_prints_the_bans_before_and_after_and_sends_nothing(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    out = io.StringIO()

    cc.run_cast_look(
        desk,
        name="Hana",
        look=_look(tmp_path, "never: a hat"),
        preview_only=True,
        out=out,
    )

    text = out.getvalue()
    assert json.dumps(["glasses"]) in text and json.dumps(["glasses", "a hat"]) in text
    assert _writes(api) == []
