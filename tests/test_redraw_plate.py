"""``plates --cast NAME --cause``: one character's plate redrawn on the regenerate route, at the plates gate."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.ops.state import load_series
from creation.production_state import load_production
from fake_api import FakeApi

REGENERATE = "/v1/spines/sp1/cast/cast_ren/regenerate"
WHOLE_CAST = "/v1/spines/sp1/cast/enrol"
NEW_URL = "https://r2.example/ren-v2.png"
CAUSE = "Ren reads forty, the brief says nineteen and lanky"


def _serve_redraw(api: FakeApi) -> None:
    def redraw(_method: str, _path: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        for asset in api.spine_doc["media_assets"]:
            if asset.get("relation_id") == "cast_ren":
                asset["url"] = NEW_URL
        return {"job_id": "job_cast_ren_2"}

    api.routes[("POST", REGENERATE)] = redraw
    api.jobs["job_cast_ren_2"] = {"job_id": "job_cast_ren_2", "status": "completed"}


def test_one_plate_is_redrawn_downloaded_booked_and_the_gate_stays_open(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    out = io.StringIO()

    path = ec.run_redraw_plate(desk, cast="ren", cause=CAUSE, out=out)

    assert path == desk / "ep01" / "plates" / "plate-ep01-2-v1.png" and path.is_file()  # Ren is cast #2
    ((body, key),) = [(b, k) for m, p, b, k in api.calls if m == "POST" and p == REGENERATE]
    assert key == f"{load_production(desk).idempotency_prefix}-plate-cast_ren-redraw-a1"
    assert body and "notes" not in json.dumps(body) and CAUSE not in json.dumps(body)  # the cause is a label only
    assert api.posted(WHOLE_CAST) == []
    assert ("download", {"url": NEW_URL, "path": str(path)}) in api.events
    assert load_production(desk).phase == "wait_plates"
    series = load_series(desk)
    assert series.spend_usd == pytest.approx(0.3)
    assert [(e.unit, e.usd, e.episode) for e in series.spend_log] == [("plate-redraw:cast_ren", 0.3, 1)]
    assert "Show it; the plates gate is still open" in out.getvalue()


def test_the_cli_takes_a_name_and_a_cause(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    with pytest.raises(SystemExit):  # --cause is required
        produce_main(["plates", "--desk", str(desk), "--cast", "Ren"])
    assert api.posted(REGENERATE) == []
    assert produce_main(["plates", "--desk", str(desk), "--cast", "Ren", "--cause", CAUSE]) == 0
    assert len(api.posted(REGENERATE)) == 1


@pytest.mark.parametrize("cause", ["", "try again", "redo"])
def test_a_redraw_without_a_real_cause_sends_nothing(desk: Path, api: FakeApi, cause: str) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    with pytest.raises(ec.CommandStopped):
        ec.run_redraw_plate(desk, cast="Ren", cause=cause, out=io.StringIO())
    assert api.posted(REGENERATE) == []
    assert load_series(desk).spend_log == []


@pytest.mark.parametrize("phase", ["wait_script", "ready_boards_enrol", "wait_board", "complete"])
def test_past_the_plates_gate_a_redraw_is_refused_before_anything_is_sent(desk: Path, api: FakeApi, phase: str) -> None:
    set_phase(desk, phase)
    _serve_redraw(api)
    with pytest.raises(ec.CommandStopped, match="only at the plates gate"):
        ec.run_redraw_plate(desk, cast="Ren", cause=CAUSE, out=io.StringIO())
    assert api.posted(REGENERATE) == []
    assert load_series(desk).spend_usd == 0


def test_an_unknown_character_is_named_and_nothing_is_sent(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_plates")
    _serve_redraw(api)
    with pytest.raises(ec.CommandStopped, match="it has: Hana, Ren"):
        ec.run_redraw_plate(desk, cast="Mira", cause=CAUSE, out=io.StringIO())
    assert api.posted(REGENERATE) == []
