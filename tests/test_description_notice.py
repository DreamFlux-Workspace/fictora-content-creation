from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from conftest import set_phase
from creation import episode_commands as ec
from creation.note_outfit import description_notices
from fake_api import FakeApi, png_bytes
from test_redraw_plate_outfit_question import REGENERATE, _serve
from test_rewrite_command import _rewritten, _serve_notes

NOTICE = "Words written for Ren (under 18) were left out of the card."


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
    ]

    assert description_notices(cards) == [NOTICE, "Hana: Some words were left out."]
