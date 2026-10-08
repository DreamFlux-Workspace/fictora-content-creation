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
from creation.plate_traits_print import (
    plate_traits_pending,
    plate_traits_text,
    wait_for_plate_traits,
)
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
            "takes_use": "sheet",
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


def test_the_gate_says_what_the_sheet_shows_and_that_takes_follow_it() -> None:
    text = plate_traits_text(_spine_with_dez())
    lines = text.splitlines()

    assert lines[0].startswith("Saved on the cards from the approved sheets")
    assert lines[1] == (
        "Dez Halloran's approved sheet shows: pale grey-white skin; long gaunt face with hollow cheeks; "
        "bald; pale grey eyes; tall and lean; wears black, charcoal, hot magenta."
    )
    assert lines[2] == (
        'Info: Dez Halloran\'s card says "long broad face", the approved sheet shows '
        '"long gaunt face with hollow cheeks" (face shape): takes follow the sheet; '
        "update the card text if the sheet is wrong."
    )
    # Noor's sheet was not read: nothing about her.
    assert "Noor" not in text
    # Plain copy: no dashes between words.
    assert not re.search(r"\s[-\u2013\u2014]\s", text)


def test_a_conflict_the_takes_settle_for_the_card_says_so_and_what_to_do() -> None:
    spine = _spine_with_dez()
    traits = copy.deepcopy(DEZ_TRAITS)
    traits["conflicts"][0]["takes_use"] = "card"
    spine["cast"][0]["observed_traits"] = traits
    line = plate_traits_text(spine).splitlines()[2]
    assert line.startswith("!! Dez Halloran's card and sheet disagree on face shape")
    assert (
        "Takes keep the card's words" in line
        and 'redraw-plate --cast "Dez Halloran"' in line
    )


def _approved(spine: dict) -> dict:
    spine = copy.deepcopy(spine)
    spine["media_assets"] = [
        {
            "relation_type": "cast_card",
            "relation_id": card["cast_id"],
            "url": f"https://r2.example/{card['cast_id']}.png",
            "review_state": "approved",
        }
        for card in spine["cast"]
    ]
    return spine


def test_the_kit_waits_briefly_for_the_background_read_and_never_sends() -> None:
    before = _approved(_spine_with_dez())
    before["cast"][0].pop("observed_traits")
    after = _approved(_spine_with_dez())
    after["cast"][1]["observed_traits"] = {**DEZ_TRAITS, "conflicts": []}
    reads = iter([before, after])
    slept: list[float] = []
    now = [0.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    got = wait_for_plate_traits(
        lambda: next(reads), before, sleep=sleep, clock=lambda: now[0]
    )
    assert got is after
    assert plate_traits_pending(got) == []
    assert len(slept) == 2


def test_the_wait_ends_at_its_limit_and_the_gate_says_the_read_is_not_back() -> None:
    spine = _approved(_spine_with_dez())
    spine["cast"][1].pop("observed_traits", None)
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds
        assert now[0] < 100, "the wait never ended"

    got = wait_for_plate_traits(
        lambda: spine, spine, wait_seconds=6, sleep=sleep, clock=lambda: now[0]
    )
    assert plate_traits_pending(got) == ["Noor Vatan"]
    assert now[0] <= 6 + 2
    text = plate_traits_text(got, wait_note=True)
    assert (
        "Sheet read not back yet for Noor Vatan" in text
        and "nothing waits for it" in text
    )
    # A story the server never reads (wait_note off, nothing saved) says nothing.
    unread = _approved({"cast": [{"cast_id": "dez", "name": "Dez"}]})
    assert plate_traits_text(unread) == ""


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
    assert "takes follow the sheet" in out


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
    assert "takes follow the sheet" in result.message


def test_a_story_without_read_sheets_prints_the_plates_yes_as_before(
    desk: Path, api: FakeApi
) -> None:
    from creation import orchestrate

    set_phase(desk, "wait_plates")
    api.routes[("POST", APPROVE)] = {"approved": True}

    result = orchestrate.approve_gate(desk, gate="plates")

    assert "approved sheet" not in result.message


def test_approving_the_plates_again_waits_for_the_background_read_on_a_new_desk(
    desk: Path,
    api: FakeApi,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import creation.plate_traits_print as plate_traits_print

    monkeypatch.setattr(plate_traits_print, "POLL_SECONDS", 0.0)
    approve_series_gate(desk, "plates", path="plates/dez-v1.png")
    set_phase(desk, "wait_script")
    api.routes[("POST", APPROVE)] = {"approved": True}
    spine = copy.deepcopy(api.spine_doc)
    for asset in spine["media_assets"]:
        if asset["relation_type"] == "cast_card":
            asset["review_state"] = "approved"
    api.spine_doc = spine
    reads = {"n": 0}
    original = api.get

    def get(path: str):
        answer = original(path)
        if path == "/v1/spines/sp1":
            reads["n"] += 1
            if reads["n"] >= 3:  # the read lands a moment after the approval
                for card in answer["cast"]:
                    card["observed_traits"] = {**DEZ_TRAITS, "conflicts": []}
        return answer

    monkeypatch.setattr(api, "get", get)

    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "plates", "--again"])
        == 0
    )

    out = capsys.readouterr().out
    assert "approved sheet shows: pale grey-white skin" in out
    assert "not back yet" not in out
    assert reads["n"] >= 3
