"""One character's plate again: ``redraw-plate --cast NAME --note`` is the only way; ``plates --cast --cause`` is retired.

``plates`` is kept as a name so muscle memory lands on a pointer: it prints the
``redraw-plate --note`` command, exits 2 and never calls the API.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.ops.state import load_series
from creation.production_state import load_production
from fake_api import FakeApi, png_bytes

REGENERATE = "/v1/spines/sp1/cast/cast_ren/regenerate"
NOTES = "/v1/spines/sp1/cast/cast_ren/notes"
CAUSE = "Ren reads forty, the brief says nineteen and lanky"


def _serve_redraw(api: FakeApi) -> None:
    def add_note(
        _method: str, _path: str, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        card = next(c for c in api.spine_doc["cast"] if c["cast_id"] == "cast_ren")
        card.setdefault("creator_notes", []).append(
            {"note_id": "n1", "text": (body or {})["text"]}
        )
        return {"spine": api.spine_doc}

    def redraw(
        _method: str, _path: str, _body: dict[str, Any] | None
    ) -> dict[str, Any]:
        for asset in api.spine_doc["media_assets"]:
            if asset.get("relation_id") == "cast_ren":
                asset["url"] = "https://r2.example/ren-v2.png"
        return {"job_id": "job_cast_ren_2"}

    api.routes[("POST", NOTES)] = add_note
    api.routes[("POST", REGENERATE)] = redraw
    api.jobs["job_cast_ren_2"] = {"job_id": "job_cast_ren_2", "status": "completed"}


@pytest.mark.parametrize(
    "argv",
    [
        ["--cast", "Ren", "--cause", CAUSE],  # the old command, as typed from memory
        ["--cast", "Ren"],
        [],
    ],
)
def test_plates_cast_points_at_redraw_plate_and_calls_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)

    code = produce_main(["plates", "--desk", str(desk), *argv])

    assert code == 2
    err = capsys.readouterr().err
    assert "retired" in err and "nothing was sent" in err
    assert f"fictora-produce redraw-plate --desk {desk}" in err and "--note" in err
    if argv:
        assert '--cast "Ren"' in err
    assert api.calls == [], "no request at all: not even the spine read"
    assert load_series(desk).spend_log == []
    assert load_production(desk).phase == "wait_plates"


def test_the_old_function_is_gone() -> None:
    assert not hasattr(ec, "run_redraw_plate")
    assert "run_redraw_plate" not in ec.__all__


def test_redraw_plate_is_the_one_redraw_and_keeps_rens_number_past_a_voice_only_character(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    api.spine_doc["cast"].insert(
        0, {"cast_id": "cast_intercom", "name": "Intercom", "voice_only": True}
    )
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "plate-ep01-1-v1.png").write_bytes(png_bytes(200))
    (plates / "plate-ep01-2-v1.png").write_bytes(png_bytes(60))

    path = ec.run_redraw_plate_with_note(
        desk, cast="Ren", note="nineteen, tall and lanky", out=io.StringIO()
    )

    assert (
        path.name == "plate-ep01-2-v2.png"
    )  # plates/ numbers drawn cast only; Ren is still #2
    assert len(api.posted(REGENERATE)) == 1
    assert [e.unit for e in load_series(desk).spend_log] == [
        "plate-note-redraw:cast_ren"
    ]


def test_a_voice_only_character_has_no_plate_to_redraw(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    api.spine_doc["cast"].append(
        {"cast_id": "cast_intercom", "name": "Intercom", "voice_only": True}
    )
    with pytest.raises(ec.CommandStopped, match="voice-only"):
        ec.run_redraw_plate_with_note(
            desk, cast="Intercom", note="older", out=io.StringIO()
        )
    assert api.posted("/v1/spines/sp1/cast/cast_intercom/regenerate") == []
    assert api.posted("/v1/spines/sp1/cast/cast_intercom/notes") == []
    assert load_series(desk).spend_log == []


def test_a_stale_spine_after_the_note_retries_the_draw_once_and_never_resends_the_note(
    desk: Path, api: FakeApi
) -> None:
    # Three Payments Late (5 Oct 2026): the note bumped the spine, the draw failed
    # plan_media_spine_version_stale, and redraw-plate stopped where step would have retried.
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    jobs = iter(["job_stale", "job_cast_ren_2"])
    api.jobs["job_stale"] = {
        "job_id": "job_stale",
        "status": "failed",
        "error": {
            "code": "plan_media_spine_version_stale",
            "message": "the spine changed",
        },
    }
    drawn = api.routes[("POST", REGENERATE)]

    def redraw(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        job = next(jobs)
        if job == "job_stale":
            return {"job_id": job}
        return drawn(method, path, body)

    api.routes[("POST", REGENERATE)] = redraw
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "plate-ep01-1-v1.png").write_bytes(png_bytes(200))

    ec.run_redraw_plate_with_note(
        desk, cast="Ren", note="nineteen, tall and lanky", out=io.StringIO()
    )

    assert len(api.posted(NOTES)) == 1, "the note is sent once"
    keys = [
        key
        for method, path, _b, key in api.calls
        if method == "POST" and path == REGENERATE
    ]
    assert len(keys) == 2 and keys[0] != keys[1], "one retry, under a fresh key"
    assert [e.unit for e in load_series(desk).spend_log] == [
        "plate-note-redraw:cast_ren"
    ]


def test_a_second_stale_answer_stops(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    api.jobs["job_cast_ren_2"] = {
        "job_id": "job_cast_ren_2",
        "status": "failed",
        "error": {
            "code": "plan_media_spine_version_stale",
            "message": "the spine changed",
        },
    }
    with pytest.raises(ec.CommandStopped, match="plan_media_spine_version_stale"):
        ec.run_redraw_plate_with_note(desk, cast="Ren", note="older", out=io.StringIO())
    assert len(api.posted(REGENERATE)) == 2
    assert len(api.posted(NOTES)) == 1
    assert load_series(desk).spend_log == []
