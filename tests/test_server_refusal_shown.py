"""The kit shows what the server says when a cast picture is refused, and the reply to an "older" note.

fictora-drama #1540: one refused cast picture fails on its own, its item on
the finished cast run is marked failed, ``failed_message`` says why and the
other pictures are kept. #1543: a note asking a young character to look older
carries the server's ``reply`` on the card.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.ops.state import load_series
from creation.production_state import load_production
from fake_api import FakeApi, png_bytes

CAST = "/v1/spines/sp1/cast/enrol"
REFUSAL = (
    "We couldn't draw Lee: the picture rules didn't allow this one. "
    "Your other pictures are kept."
)


def _with_newcomers(spine: dict[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(spine)
    after["cast"].extend(
        {"cast_id": cast_id, "name": name, "intro_episode_ordinal": 2}
        for cast_id, name in (("cast_kai", "Kai"), ("cast_lee", "Lee"))
    )
    after["beats"][1]["dialogue_lines"][0]["cast_id"] = "cast_kai"
    after["beats"][1]["dialogue_lines"][1]["cast_id"] = "cast_lee"
    return after


def _kai_drawn_lee_not(desk: Path, api: FakeApi, terminal: dict[str, Any]) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    ec.run_author(desk, episode=2, out=io.StringIO())
    api.spine_doc = _with_newcomers(api.spine_doc)
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.jobs["job_cast"] = {"status": "completed", **terminal}

    def enrol(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc["media_assets"].append(
            {
                "relation_type": "cast_card",
                "relation_id": "cast_kai",
                "url": "https://r2.example/kai.png",
            }
        )
        return {"job_id": "job_cast"}

    api.routes[("POST", CAST)] = enrol


def _refused_terminal() -> dict[str, Any]:
    return {
        "steps": [
            {
                "id": "cast",
                "label": "Drawing your cast",
                "state": "done",
                "items": [
                    {"label": "Kai", "state": "done"},
                    {"label": "Lee", "state": "failed", "refused": True},
                ],
            }
        ],
        "failed_message": REFUSAL,
        "retry": {"offered": True, "resumes": True},
    }


def test_a_refused_picture_prints_the_servers_words_and_no_redraw_hint(
    desk: Path, api: FakeApi
) -> None:
    _kai_drawn_lee_not(desk, api, _refused_terminal())

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert REFUSAL in result.message
    assert "redraw-plate" not in result.message
    assert "earlier cast run" not in result.message
    assert "Cast drawn" not in result.message
    assert load_production(desk).phase == "ready_cast_enrol"


def test_the_pictures_that_drew_are_still_downloaded_and_booked(
    desk: Path, api: FakeApi
) -> None:
    _kai_drawn_lee_not(desk, api, _refused_terminal())

    orchestrate.run_step(desk, confirm_spend=True)

    assert [p.name for p in (desk / "ep02" / "plates").glob("plate-*")] == [
        "plate-ep02-1-v1.png"
    ]
    assert [e.unit for e in load_series(desk).spend_log] == ["plates x1"]


def test_no_picture_and_no_refusal_still_says_it_was_an_earlier_cast_run(
    desk: Path, api: FakeApi
) -> None:
    _kai_drawn_lee_not(desk, api, {})

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert "No picture came back for Lee" in result.message
    assert "the server answered with an earlier cast run" in result.message
    assert '--cast "Lee"' in result.message


def test_the_servers_reply_to_an_older_note_is_printed(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "plate-ep01-1-v1.png").write_bytes(png_bytes(200))
    (plates / "plate-ep01-2-v1.png").write_bytes(png_bytes(60))
    reply = "Drew Ren a few years older, around 14."
    notes = "/v1/spines/sp1/cast/cast_ren/notes"

    def add_note(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        card = next(c for c in api.spine_doc["cast"] if c["cast_id"] == "cast_ren")
        card.setdefault("creator_notes", []).append(
            {"note_id": "n1", "text": (body or {})["text"], "reply": reply}
        )
        api.spine_doc["spine_version"] = "v6"
        return {"spine": api.spine_doc}

    def redraw(_m: str, _p: str, _b: dict[str, Any] | None) -> dict[str, Any]:
        for asset in api.spine_doc["media_assets"]:
            if asset.get("relation_id") == "cast_ren":
                asset["url"] = "https://r2.example/ren-v2.png"
        return {"job_id": "job_ren"}

    api.routes[("POST", notes)] = add_note
    api.routes[("POST", "/v1/spines/sp1/cast/cast_ren/regenerate")] = redraw
    api.jobs["job_ren"] = {"job_id": "job_ren", "status": "completed"}
    out = io.StringIO()

    ec.run_redraw_plate_with_note(desk, cast="Ren", note="Make her look older", out=out)

    assert reply in out.getvalue()
