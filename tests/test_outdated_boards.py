"""Boards showing an older picture of a story object (fictora-drama #787), on the fake API.

After an object is redrawn and its new picture approved, the boards drawn with
the old picture are listed on the story (``outdated_boards``). The kit says so
in plain words with the price and the redraw command per board, and never
redraws by itself (founder, 13 Oct 2026: never block, the human decides).
``redraw-board`` counts the outdated object as a change, so it needs no note
or label. A board held while the object's redraw is pending (``422
cast_not_approved`` with ``prop_reasons: redraw_pending``) is said plainly.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import orchestrate
from creation import prop_commands as pc
from creation.cli_produce import main as produce_main
from creation.harness.http_util import api_error_text
from creation.harness.prop_hold_refusal import prop_hold_text
from creation.ops.floor import approve_series_gate
from creation.ops.state import episode_by_ordinal, load_series
from creation.outdated_boards import outdated_boards, outdated_boards_notice
from creation.production_state import load_production
from creation.spine_view import board_inputs, frames_by_set, frames_digest
from fake_api import FakeApi

APPROVE = "/v1/spines/sp1/cast/approve"
BOARDS = "/v1/spines/sp1/boards/enrol"
REGEN = "/v1/spines/sp1/episodes/1/boards/1/regenerate"
PLATE = "cracked white plate"
OUTDATED = [
    {
        "asset_id": "asset_ep02_set03",
        "episode_id": "ep_02",
        "episode_ordinal": 2,
        "set_index": 3,
        "outdated_objects": [{"prop_id": "prop_vase", "name": "blue vase"}],
    },
    {
        "asset_id": "asset_episode_01_set01",
        "episode_id": "episode_01",
        "episode_ordinal": 1,
        "set_index": 1,
        "outdated_objects": [{"prop_id": "prop_plate", "name": PLATE}],
    },
]
HELD = {
    "error": {
        "code": "cast_not_approved",
        "message": "an object the episode shows has a redraw pending",
        "details": {
            "prop_reasons": {"prop_plate": "redraw_pending"},
            "unapproved_prop_ids": ["prop_plate"],
            "unapproved_objects": [{"prop_id": "prop_plate", "name": PLATE}],
        },
    }
}


def _outdated(api: FakeApi, rows: list[dict[str, Any]] | None = None) -> None:
    api.spine_doc["outdated_boards"] = OUTDATED if rows is None else rows


def _drawn(desk: Path, api: FakeApi) -> None:
    """Record ep01 t1 as drawn from the story as it is now; serve the regenerate route."""

    set_phase(
        desk,
        "wait_board",
        board_digests={
            "ep01-t1": frames_digest(frames_by_set(api.spine_doc, episode=1)[1])
        },
        board_inputs={
            "ep01-t1": board_inputs(api.spine_doc, episode=1, set_index=1, take_count=1)
        },
    )
    api.routes[("POST", REGEN)] = {"job_id": "job_redraw"}
    api.jobs["job_redraw"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 30.0}]
    }


def _save_story(desk: Path, api: FakeApi) -> None:
    (desk / "api").mkdir(parents=True, exist_ok=True)
    (desk / "api" / "spine.json").write_text(
        json.dumps(api.spine_doc), encoding="utf-8"
    )


def _paid(api: FakeApi) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _, _ in api.calls if m != "GET"]


# --- the notice ---------------------------------------------------------------------------


def test_the_notice_names_each_board_its_object_the_price_and_the_command() -> None:
    text = outdated_boards_notice({"outdated_boards": OUTDATED}, desk="D")

    assert text is not None
    first, *rest = text.splitlines()
    assert first == (
        f"Boards showing an older picture of an object: ep01 board 1 ({PLATE}), ep02 board 3 (blue vase). "
        "They keep the old look unless redrawn; each board redraw is $0.30. Human's call."
    )
    assert "Only after the human's yes" in rest[0]
    assert rest[1:] == [
        "    fictora-produce redraw-board --desk D --episode 1 --take t1",
        "    fictora-produce redraw-board --desk D --episode 2 --take t3",
    ]


@pytest.mark.parametrize("spine", [None, {}, {"outdated_boards": []}])
def test_no_listed_board_says_nothing(spine: dict[str, Any] | None) -> None:
    assert outdated_boards(spine) == []
    assert outdated_boards_notice(spine) is None


def test_approving_the_plates_says_which_boards_show_the_old_picture(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    api.routes[("POST", APPROVE)] = {"spine_version": "v6"}
    _outdated(api)

    result = orchestrate.approve_gate(desk, gate="plates")

    assert f"ep01 board 1 ({PLATE})" in result.message
    assert "each board redraw is $0.30. Human's call." in result.message
    assert f"redraw-board --desk {desk} --episode 1 --take t1" in result.message
    # Said, never redrawn.
    assert api.posted(REGEN) == [] and api.posted(BOARDS) == []


def test_approving_the_plates_again_says_it_too(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    approve_series_gate(desk, "plates", path="plates/plate-v2.png")
    set_phase(desk, "wait_board")
    api.routes[("POST", APPROVE)] = {"approved": True}
    _outdated(api)

    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "plates", "--again"])
        == 0
    )

    out = capsys.readouterr().out
    assert "Boards showing an older picture of an object: ep01 board 1" in out
    assert "--episode 2 --take t3" in out
    assert api.posted(REGEN) == []


def test_props_and_status_say_it(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _outdated(api)
    api.spine_doc["props"] = [{"prop_id": "prop_plate", "name": PLATE}]
    out = io.StringIO()

    pc.run_props(desk, out=out)

    assert f"ep01 board 1 ({PLATE})" in out.getvalue()
    _save_story(desk, api)
    assert produce_main(["status", "--desk", str(desk)]) == 0
    assert "Human's call." in capsys.readouterr().out
    assert _paid(api) == []


def test_status_without_listed_boards_is_one_line(desk: Path) -> None:
    assert "\n" not in orchestrate.status_message(desk)


# --- redraw-board -------------------------------------------------------------------------


def test_an_outdated_board_redraws_with_no_note_or_label(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    """The board's own inputs read the same; the outdated object is the change (mutation-checked)."""

    _drawn(desk, api)
    _outdated(api)
    _save_story(desk, api)

    code = produce_main(
        ["redraw-board", "--desk", str(desk), "--episode", "1", "--take", "t1"]
    )

    assert code == 0
    assert len(api.posted(REGEN)) == 1
    out = capsys.readouterr().out
    assert (
        f"t1 redraws with changed: a newer picture of {PLATE} (the board shows the older one)"
        in out
    )
    redraws = episode_by_ordinal(load_series(desk), 1).takes[0].extra["redraws"]
    assert redraws[0]["reroll"] is False
    assert redraws[0]["cause"] == f"newer picture of {PLATE}"
    assert load_production(desk).phase == "wait_board"


def test_an_outdated_board_with_a_label_redraws_too(desk: Path, api: FakeApi) -> None:
    _drawn(desk, api)
    _outdated(api)

    assert produce_main(
        ["redraw-board", "--desk", str(desk), "--episode", "1", "--take", "t1",
         "--cause", "new plate"]
    ) == 0  # fmt: skip
    assert len(api.posted(REGEN)) == 1


def test_a_board_not_listed_still_stops_unpaid(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(desk, api)
    _outdated(api, [OUTDATED[0]])  # only ep02 t3 is listed

    assert produce_main(
        ["redraw-board", "--desk", str(desk), "--episode", "1", "--take", "t1",
         "--cause", "same again"]
    ) == 2  # fmt: skip
    assert "nothing this board is drawn from has changed" in capsys.readouterr().err
    assert _paid(api) == []


def test_no_label_on_a_board_the_live_story_no_longer_lists_stops_unpaid(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(desk, api)
    _outdated(api)
    _save_story(desk, api)  # the desk's copy still lists it
    api.spine_doc.pop("outdated_boards")  # already redrawn elsewhere

    assert produce_main(
        ["redraw-board", "--desk", str(desk), "--episode", "1", "--take", "t1"]
    ) == 2  # fmt: skip
    assert "--cause is required" in capsys.readouterr().err
    assert _paid(api) == []


# --- held boards (422) --------------------------------------------------------------------


def test_a_board_held_for_a_pending_redraw_is_said_plainly() -> None:
    text = api_error_text(HELD)

    assert (
        f"Boards are held: {PLATE} is waiting for its redraw. Draw it (`redraw-prop … --confirm-spend`) "
        "and approve the plates first." in text
    )
    assert f'redraw-prop --desk D --prop "{PLATE}"' in text
    assert "--gate plates" in text


def test_a_plain_cast_not_approved_is_not_said_as_an_object_hold() -> None:
    assert prop_hold_text("cast_not_approved", {"cast_ids": ["cast_hana"]}) is None
    assert (
        prop_hold_text("cast_not_approved", {"prop_reasons": {"p": "no_picture"}})
        is None
    )
    assert prop_hold_text("board_behind_script_edit", HELD["error"]["details"]) is None


def test_the_boards_step_held_for_an_object_does_not_say_approve_the_same_plates(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", BOARDS)] = SystemExit(
        f"HTTP 422 POST /v1/spines/sp1/boards/enrol: {api_error_text(HELD)}"
    )

    with pytest.raises(SystemExit) as raised:
        orchestrate.run_step(desk, confirm_spend=True)

    said = str(raised.value.code)
    assert f"Boards are held: {PLATE} is waiting for its redraw" in said
    assert "server lost the plates approval" not in said
    with pytest.raises(RuntimeError) as again:
        orchestrate.run_step(desk)
    assert "server lost the plates approval" not in str(again.value)
    assert "Boards are held" in str(again.value)
