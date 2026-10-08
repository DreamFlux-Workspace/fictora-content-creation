"""The cast gate prints what each approved sheet shows and where it disagrees with the card.

fictora-drama reads each new story's approved plate once at cast approval and
saves ``observed_traits`` on the card (founder decision, 8 Oct 2026). The kit
only prints what the server saved: no request, no cost.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from conftest import set_phase
from creation.cli_produce import main as produce_main
from creation.ops.floor import approve_series_gate
from creation.plate_traits_print import plate_traits_text
from fake_api import FakeApi

APPROVE = "/v1/spines/sp1/cast/approve"

DEZ_TRAITS = {
    "source": "approved_plate_read",
    "plate_digest": "sha256:" + "a" * 64,
    "plate_asset_id": "asset_plan_cast_dez",
    "read_at": "2026-10-08T12:00:00Z",
    "skin_tone": "pale grey-white",
    "face_shape": "long gaunt face with hollow cheeks",
    "hair_length": "bald",
    "eye_colour": "pale grey",
    "build": "tall and lean",
    "outfit_colours": ["black", "charcoal", "hot magenta"],
    "conflicts": [
        {
            "trait": "face_shape",
            "card_says": "long broad face",
            "plate_shows": "long gaunt face with hollow cheeks",
        }
    ],
}


def _spine_with_dez() -> dict:
    return {
        "spine_version": "sha256:" + "b" * 64,
        "cast": [
            {"cast_id": "dez", "name": "Dez Halloran", "observed_traits": DEZ_TRAITS},
            {"cast_id": "noor", "name": "Noor Vatan"},
        ],
    }


def test_the_gate_says_what_the_sheet_shows_and_flags_a_disagreement() -> None:
    text = plate_traits_text(_spine_with_dez())
    lines = text.splitlines()

    assert lines[0].startswith("Saved on the cards from the approved sheets")
    assert lines[1] == (
        "Dez Halloran's approved sheet shows: pale grey-white skin; long gaunt face with hollow cheeks; "
        "bald; pale grey eyes; tall and lean; wears black, charcoal, hot magenta."
    )
    assert lines[2].startswith(
        "!! Dez Halloran's card and sheet disagree on face shape"
    )
    assert '"long broad face"' in lines[2] and "Takes keep the card's words" in lines[2]
    assert 'redraw-plate --cast "Dez Halloran"' in lines[2]
    # Noor's sheet was not read: nothing about her.
    assert "Noor" not in text
    # Plain copy: no dashes between words.
    assert not re.search(r"\s[-–—]\s", text)


def test_nothing_is_printed_when_no_sheet_was_read() -> None:
    assert plate_traits_text(None) == ""
    assert plate_traits_text({"cast": [{"cast_id": "dez", "name": "Dez"}]}) == ""


def test_approving_the_plates_again_prints_the_sheet_traits(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    approve_series_gate(desk, "plates", path="plates/dez-v1.png")
    set_phase(desk, "wait_script")
    api.routes[("POST", APPROVE)] = {"approved": True}
    spine = copy.deepcopy(api.spine_doc)
    spine["cast"][0]["observed_traits"] = DEZ_TRAITS
    api.spine_doc = spine

    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "plates", "--again"])
        == 0
    )

    out = capsys.readouterr().out
    assert "approved sheet shows: pale grey-white skin" in out
    assert "disagree on face shape" in out


def test_the_first_plates_yes_prints_the_sheet_traits(desk: Path, api: FakeApi) -> None:
    from creation import orchestrate

    set_phase(desk, "wait_plates")
    api.routes[("POST", APPROVE)] = {"approved": True}
    spine = copy.deepcopy(api.spine_doc)
    spine["cast"][0]["observed_traits"] = DEZ_TRAITS
    api.spine_doc = spine

    result = orchestrate.approve_gate(desk, gate="plates")

    assert "Human: approve script lines." in result.message
    assert "approved sheet shows: pale grey-white skin" in result.message
    assert "disagree on face shape" in result.message


def test_a_story_without_read_sheets_prints_the_plates_yes_as_before(
    desk: Path, api: FakeApi
) -> None:
    from creation import orchestrate

    set_phase(desk, "wait_plates")
    api.routes[("POST", APPROVE)] = {"approved": True}

    result = orchestrate.approve_gate(desk, gate="plates")

    assert "approved sheet" not in result.message
