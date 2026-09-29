"""``redraw-board --note``: the note becomes shot edits (the app's director path) before the take is redrawn.

Production, 2026-09-29: ``redraw-board --take t2 --cause "medium two-shot ... no map"`` ran three
times and drew the same close two-shot with a map each time ($0.30 each), because ``--cause`` is
only a label and the redraw reused unchanged shot descriptions.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation.board_note import regenerate_takes_note, take_patch
from creation.cli_produce import main as produce_main
from creation.ops.state import episode_by_ordinal, load_series
from creation.spine_view import frames_by_set, frames_digest
from fake_api import FakeApi, openapi_doc

REGEN = "/v1/spines/sp1/episodes/1/boards/1/regenerate"
TURNS = "/v1/spines/sp1/director/turns"
NOTE = "medium two-shot walking down the hallway, waist-up, no map"
NEW_INTENT = (
    "Medium two-shot, waist-up: Hana and Ren walk down the hallway; no map anywhere."
)


def _record_digest(desk: Path, api: FakeApi) -> None:
    digest = frames_digest(frames_by_set(api.spine_doc, episode=1)[1])
    set_phase(desk, "wait_board", board_digests={"ep01-t1": digest})


def _regen_routes(api: FakeApi) -> None:
    def regenerate(method: str, path: str, body: dict | None) -> dict:
        # The server re-authors the take's frames from the edited beat before it draws.
        if any(f.get("edited_beat_ids") for f in api.spine_doc["frames"]):
            for frame in api.spine_doc["frames"]:
                if frame["episode_id"] == "episode_01":
                    frame["visual_brief"]["shot_scale"] = "medium two-shot"
                    frame.pop("edited_beat_ids", None)
        return {"job_id": "job_redraw"}

    api.routes[("POST", REGEN)] = regenerate
    api.jobs["job_redraw"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 30.0}]
    }


def _cascade_routes(api: FakeApi) -> None:
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = {
        "proposal_id": "prop_1",
        "items": [
            {
                "item_id": "regen_board_episode_01_set01",
                "recipe_id": "drama.pilot_board_3x3_v1",
                "estimated_tier": "media",
                "selected": True,
            }
        ],
    }

    def execute(method: str, path: str, body: dict | None) -> dict:
        preview = api.posted("/v1/spines/sp1/cascade/preview")[-1] or {}
        for edit in preview["edit"]["patch"].get("beats") or []:
            for beat in api.spine_doc["beats"]:
                if beat["beat_id"] == edit["beat_id"]:
                    beat["motion_intent"] = edit["motion_intent"]
        for frame in api.spine_doc["frames"]:
            if frame["episode_id"] == "episode_01":
                frame["edited_beat_ids"] = ["beat_episode_01_01"]
        return {"stale_storyboard_sets": [{"episode_ordinal": 1, "set_index": 1}]}

    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = execute


def _turn(*steps: dict[str, Any], reply: str = "Restaged it.") -> dict[str, Any]:
    return {
        "turn_id": "turn_1",
        "spine_version": "v5",
        "reply": reply,
        "steps": list(steps),
    }


def _call_order(api: FakeApi, path: str) -> int:
    return next(
        i for i, (m, p, _, _) in enumerate(api.calls) if m == "POST" and p == path
    )


def test_a_note_becomes_shot_edits_through_the_director_is_printed_per_row_then_redrawn(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _cascade_routes(api)
    api.routes[("POST", TURNS)] = _turn(
        {
            "step_id": "s1",
            "tool": "patch_story",
            "summary": "Changing 2 beats after approval touches pilot board 3x3: waiting for you to confirm.",
            "state": "needs_confirmation",
            "input": {
                "episode_id": "episode_01",
                "beats": [
                    {"beat_id": "beat_episode_01_01", "motion_intent": NEW_INTENT},
                    {
                        "beat_id": "beat_ep_02_01",
                        "motion_intent": "Something else entirely.",
                    },
                ],
                "dialogue_lines": [
                    {"line_id": "line_episode_01_01", "text": "Closed."}
                ],
            },
        },
        {
            "step_id": "s2",
            "tool": "redraw_take",
            "summary": "Redraw take 1",
            "state": "pending",
            "input": {},
        },
    )
    out = io.StringIO()

    ec.run_redraw_board(desk, episode=1, take_id="t1", note=NOTE, out=out)

    turn = api.posted(TURNS)[0]
    assert turn["stage"] == "storyboard" and turn["episode_id"] == "episode_01"
    assert NOTE in turn["message"] and "beat_episode_01_01" in turn["message"]
    # Only this take's beat goes through the cascade; paid items stay off.
    preview = api.posted("/v1/spines/sp1/cascade/preview")[0]
    assert preview["edit"]["patch"] == {
        "beats": [{"beat_id": "beat_episode_01_01", "motion_intent": NEW_INTENT}]
    }
    execute = api.posted("/v1/spines/sp1/cascade/execute")[0]
    assert execute["items"] == [
        {"item_id": "regen_board_episode_01_set01", "selected": False}
    ]
    # The edit lands before the redraw is sent.
    assert _call_order(api, "/v1/spines/sp1/cascade/execute") < _call_order(api, REGEN)
    text = out.getvalue()
    assert "t1 shot changes from the note" in text
    assert (
        "row 1 (beat_episode_01_01)" in text
        and "was: Hana wipes the counter (ep 1)" in text
    )
    assert f"now: {NEW_INTENT}" in text
    assert "left out: beat_ep_02_01 is not in this take" in text
    assert "rows that changed in this redraw" in text and "medium two-shot" in text
    assert "notes" not in (api.posted(REGEN)[0] or {}) and "note" not in (
        api.posted(REGEN)[0] or {}
    )
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == pytest.approx(0.30)


def test_a_note_that_changes_no_shot_stops_before_anything_is_drawn(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    api.routes[("POST", TURNS)] = _turn(reply="Which part of the take do you mean?")

    with pytest.raises(ec.CommandStopped, match="did not change any shot of t1"):
        ec.run_redraw_board(desk, episode=1, take_id="t1", note=NOTE, out=io.StringIO())

    assert api.posted(REGEN) == []
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == 0.0


def test_a_deploy_whose_regenerate_route_takes_a_note_gets_it_on_the_redraw(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    doc = openapi_doc()
    doc["components"]["schemas"]["DramaBoardRegenerateRequest"] = {
        "properties": {"note": {}, "episode_count": {}}
    }
    doc["paths"] = {
        "/v1/spines/{spine_id}/episodes/{ordinal}/boards/{set_index}/regenerate": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/DramaBoardRegenerateRequest"
                            }
                        }
                    }
                }
            }
        }
    }
    api.routes[("GET", "/openapi.json")] = doc
    out = io.StringIO()

    ec.run_redraw_board(desk, episode=1, take_id="t1", note=NOTE, out=out)

    assert api.posted(TURNS) == []
    assert (api.posted(REGEN)[0] or {})["note"] == NOTE


def test_without_a_note_an_unchanged_take_is_refused_loudly_until_same_shots(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    argv = [
        "redraw-board",
        "--desk",
        str(desk),
        "--episode",
        "1",
        "--take",
        "t1",
        "--cause",
        "x",
    ]

    assert produce_main(argv) == 2

    err = capsys.readouterr().err
    assert (
        "!! t1:" in err
        and "--note" in err
        and "--frame N --set" in err
        and "--same-shots" in err
    )
    assert api.posted(REGEN) == []
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == 0.0
    assert produce_main([*argv, "--same-shots"]) == 0
    assert len(api.posted(REGEN)) == 1


def test_the_note_alone_is_enough_and_becomes_the_label(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _cascade_routes(api)
    api.routes[("POST", TURNS)] = _turn(
        {
            "step_id": "s1",
            "tool": "patch_story",
            "summary": "Changing 1 beat",
            "state": "needs_confirmation",
            "input": {
                "beats": [
                    {"beat_id": "beat_episode_01_01", "motion_intent": NEW_INTENT}
                ]
            },
        }
    )
    argv = [
        "redraw-board",
        "--desk",
        str(desk),
        "--episode",
        "1",
        "--take",
        "t1",
        "--note",
        NOTE,
    ]

    assert produce_main(argv) == 0

    redraw = episode_by_ordinal(load_series(desk), 1).takes[0].extra["redraws"][0]
    assert redraw["cause"] == NOTE and redraw["note"] == NOTE


def test_helpers_read_the_deploy_and_the_turn_conservatively() -> None:
    assert regenerate_takes_note(openapi_doc()) is False
    assert regenerate_takes_note(None) is False
    done = {
        "steps": [
            {
                "tool": "patch_story",
                "state": "done",
                "input": {"beats": [{"beat_id": "b1", "motion_intent": "x"}]},
            }
        ]
    }
    assert take_patch(done, allowed_beat_ids={"b1"}) == ({}, [])
