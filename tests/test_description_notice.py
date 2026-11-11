from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.note_outfit import description_notices
from fake_api import FakeApi, png_bytes
from test_redraw_plate_outfit_question import REGENERATE, _serve
from test_rewrite_command import _rewritten, _serve_notes

NOTICE = "Words written for Ren (under 18) were left out of the card."
KANJI_NOTICE = (
    "I left out a few words from your description of 花子, since 花子 is under 18."
)


def _ren(api: FakeApi) -> dict[str, Any]:
    return next(c for c in api.spine_doc["cast"] if c["cast_id"] == "cast_ren")


def test_a_rewrite_prints_the_notice_on_a_card(desk: Path, api: FakeApi) -> None:
    _serve_notes(api)
    _rewritten(api, "job_rw_1")
    _ren(api)["description_notice"] = NOTICE
    out = io.StringIO()

    ec.run_rewrite(desk, episode=2, line="Ren swaps the sugar.", out=out)

    assert out.getvalue().count(NOTICE) == 1


def test_a_rewrite_without_a_notice_prints_nothing_extra(
    desk: Path, api: FakeApi
) -> None:
    _serve_notes(api)
    _rewritten(api, "job_rw_1")
    out = io.StringIO()

    ec.run_rewrite(desk, episode=2, line="Ren swaps the sugar.", out=out)

    assert "left out" not in out.getvalue()


def test_a_plate_note_prints_the_notice_on_that_card(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_plates")
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "plate-ep01-1-v1.png").write_bytes(png_bytes(200))
    (plates / "plate-ep01-2-v1.png").write_bytes(png_bytes(60))
    _serve(api)
    _ren(api)["description_notice"] = NOTICE
    out = io.StringIO()

    ec.run_redraw_plate_with_note(
        desk, cast="Ren", note="Her braid is longer.", out=out
    )

    assert out.getvalue().count(NOTICE) == 1
    assert api.posted(REGENERATE)


def test_the_name_leads_only_when_the_servers_words_lack_it() -> None:
    cards = [
        {"name": "Ren", "description_notice": NOTICE},
        {"name": "Hana", "description_notice": "Some words were left out."},
        {"name": "Kai", "description_notice": ""},
        {"name": "Mio"},
        {"name": "Al", "description_notice": "Also, some words were left out."},
        {"name": "花子", "description_notice": KANJI_NOTICE},
    ]

    assert description_notices(cards) == [
        NOTICE,
        "Hana: Some words were left out.",
        "Al: Also, some words were left out.",
        KANJI_NOTICE,
    ]


def test_the_draft_step_prints_the_notice_and_notes_it(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    api.jobs["job_plan"] = {"status": "completed", "result": {}}
    _ren(api)["description_notice"] = NOTICE
    set_phase(desk, "new", spine_id=None)

    result = orchestrate.run_step(desk)

    assert result.phase == "ready_cast_enrol"
    assert result.message.count(NOTICE) == 1
    assert NOTICE in (desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")


LEFT_OUT = "I left your note “make her 25” out of Ren's picture, since Ren is under 18."


def test_a_note_the_picture_leaves_out_prints_beside_the_description_notice() -> None:
    card = {"name": "Ren", "description_notice": NOTICE, "note_notice": LEFT_OUT}

    assert description_notices([card]) == [NOTICE, LEFT_OUT]


def test_a_note_notice_alone_prints_and_an_older_server_prints_nothing() -> None:
    assert description_notices([{"name": "Ren", "note_notice": LEFT_OUT}]) == [LEFT_OUT]
    assert description_notices([{"name": "Ren"}]) == []


def test_a_plate_note_prints_the_left_out_line_on_that_card(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "plate-ep01-1-v1.png").write_bytes(png_bytes(200))
    (plates / "plate-ep01-2-v1.png").write_bytes(png_bytes(60))
    _serve(api)
    _ren(api)["note_notice"] = LEFT_OUT
    out = io.StringIO()

    ec.run_redraw_plate_with_note(
        desk, cast="Ren", note="Her braid is longer.", out=out
    )

    assert out.getvalue().count(LEFT_OUT) == 1
