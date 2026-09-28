"""The look gate: ``approve --gate look`` records and pins; ``step`` holds paid drawings for a drawn, unapproved look."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from creation import episode_commands as ec
from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.ops.floor import approve_series_gate
from creation.ops.state import load_series
from creation.production_state import load_production
from conftest import set_phase
from fake_api import FakeApi

FRAME_ROUTE = "/v1/spines/sp1/look-frame"
PIN_ROUTE = "/v1/spines/sp1/look-register"
URL_1 = "https://cdn.example/look-frame/v1.png"
URL_2 = "https://cdn.example/look-frame/v2.png"


def _answer(url: str) -> dict[str, object]:
    return {"image_url": url, "cached": False, "cost_usd": 0.3}


def _pin_answers(api: FakeApi) -> None:
    """The pin route; the story then carries the pinned URL (as the server's spine does)."""

    def pin(_method: str, _path: str, body: dict[str, object] | None) -> dict[str, str]:
        assert body is not None
        api.spine_doc["look_register_url"] = body["url"]
        return {"spine_id": "sp1"}

    api.routes[("POST", PIN_ROUTE)] = pin


def _draw(desk: Path, api: FakeApi, *urls: str) -> list[Path]:
    answers = [_answer(url) for url in urls]
    api.routes[("POST", FRAME_ROUTE)] = lambda *_: answers.pop(0)
    return [
        ec.run_look_frame(desk, description=f"look number {n}", out=io.StringIO())
        for n in range(1, len(urls) + 1)
    ]


def _paid_calls(api: FakeApi) -> list[str]:
    return [path for method, path, _b, _k in api.calls if method != "GET"]


# --- approve --gate look ---------------------------------------------------------------------


def test_approve_look_takes_the_newest_frame_records_the_yes_and_pins_once(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _draw(desk, api, URL_1, URL_2)
    _pin_answers(api)

    assert produce_main(["approve", "--desk", str(desk), "--gate", "look"]) == 0

    printed = capsys.readouterr().out
    assert "Using the newest look frame: shared/look/look-frame-v2.png" in printed
    assert api.posted(PIN_ROUTE) == [{"spine_version": "v5", "url": URL_2}]
    look = load_series(desk).look
    assert look.status == "approved"
    assert look.path == "shared/look/look-frame-v2.png"
    assert look.note == f"pinned {URL_2}"


def test_approve_look_is_idempotent(desk: Path, api: FakeApi) -> None:
    _draw(desk, api, URL_1)
    _pin_answers(api)

    first = ec.run_approve_look(desk, out=io.StringIO())
    out = io.StringIO()
    second = ec.run_approve_look(desk, out=out)

    assert len(api.posted(PIN_ROUTE)) == 1
    assert "already approved and pinned" in out.getvalue()
    assert second.at_utc == first.at_utc


def test_approve_look_after_a_plain_pin_records_without_pinning_again(
    desk: Path, api: FakeApi
) -> None:
    _draw(desk, api, URL_1)
    _pin_answers(api)
    ec.run_look(desk, url=URL_1, out=io.StringIO())

    ec.run_approve_look(desk, url=URL_1, out=io.StringIO())

    assert len(api.posted(PIN_ROUTE)) == 1
    assert load_series(desk).look.status == "approved"


def test_approve_look_by_path_reads_that_frames_url(desk: Path, api: FakeApi) -> None:
    first, _second = _draw(desk, api, URL_1, URL_2)
    _pin_answers(api)

    ec.run_approve_look(desk, path=first, out=io.StringIO())

    assert api.posted(PIN_ROUTE) == [{"spine_version": "v5", "url": URL_1}]
    assert load_series(desk).look.path == "shared/look/look-frame-v1.png"


def test_an_older_desk_frame_finds_its_url_in_the_same_version_answer(
    desk: Path, api: FakeApi
) -> None:
    (frame,) = _draw(desk, api, URL_1)
    (frame.parent / "look-frames.json").unlink()
    _pin_answers(api)

    ec.run_approve_look(desk, out=io.StringIO())

    assert api.posted(PIN_ROUTE) == [{"spine_version": "v5", "url": URL_1}]


def test_approve_look_with_no_frame_and_no_url_stops_before_any_call(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert produce_main(["approve", "--desk", str(desk), "--gate", "look"]) == 2
    assert "no look frame on this desk" in capsys.readouterr().err
    assert api.calls == []
    assert load_series(desk).look.status == "pending"


def test_a_refused_pin_records_no_yes(desk: Path, api: FakeApi) -> None:
    _draw(desk, api, URL_1)
    api.routes[("POST", PIN_ROUTE)] = SystemExit("HTTP 409 POST x: spine_version_stale")

    with pytest.raises(SystemExit):
        ec.run_approve_look(desk, out=io.StringIO())

    assert load_series(desk).look.status == "pending"


def test_url_is_refused_on_other_gates(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    set_phase(desk, "wait_plates")
    argv = ["approve", "--desk", str(desk), "--gate", "plates", "--url", URL_1]
    assert produce_main(argv) == 2
    assert "--url is for --gate look only" in capsys.readouterr().err


# --- plain look --url ------------------------------------------------------------------------


def test_plain_look_pins_says_the_gate_is_open_and_does_not_approve(
    desk: Path, api: FakeApi
) -> None:
    _pin_answers(api)
    out = io.StringIO()

    ec.run_look(desk, url=URL_1, out=out)

    assert api.posted(PIN_ROUTE) == [{"spine_version": "v5", "url": URL_1}]
    printed = out.getvalue()
    assert "the look gate is still open" in printed
    assert f"approve --desk {desk.resolve()} --gate look --url {URL_1}" in printed
    assert load_series(desk).look.status == "pending"


def test_plain_look_on_an_approved_look_prints_no_gate_note(
    desk: Path, api: FakeApi
) -> None:
    _pin_answers(api)
    approve_series_gate(desk, "look")
    out = io.StringIO()

    ec.run_look(desk, url=URL_1, out=out)

    assert "still open" not in out.getvalue()


def test_look_frame_points_at_approve_and_indexes_the_url(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", FRAME_ROUTE)] = _answer(URL_1)
    out = io.StringIO()

    ec.run_look_frame(desk, description="teal night", out=out)

    assert f"approve --desk {desk.resolve()} --gate look" in out.getvalue()
    index = json.loads((desk / "shared" / "look" / "look-frames.json").read_text())
    assert index == {"look-frame-v1.png": URL_1}


# --- step holds paid drawings ----------------------------------------------------------------


@pytest.mark.parametrize("phase", ["ready_cast_enrol", "ready_boards_enrol"])
def test_step_refuses_paid_drawings_while_a_drawn_look_is_unapproved(
    desk: Path, api: FakeApi, phase: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _draw(desk, api, URL_1)
    api.calls.clear()
    set_phase(desk, phase)

    assert produce_main(["step", "--desk", str(desk)]) == 2

    err = capsys.readouterr().err
    assert "look gate is open" in err and "shared/look/look-frame-v1.png" in err
    assert "--gate look" in err
    assert api.calls == []
    assert load_production(desk).phase == phase


def test_step_draws_plates_once_the_look_is_approved(desk: Path, api: FakeApi) -> None:
    _draw(desk, api, URL_1)
    _pin_answers(api)
    ec.run_approve_look(desk, out=io.StringIO())
    set_phase(desk, "ready_cast_enrol")
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    result = orchestrate.run_step(desk)

    assert result.phase == "wait_plates"
    assert api.posted("/v1/spines/sp1/cast/enrol")


def test_a_desk_that_never_drew_a_look_frame_is_not_held(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_cast_enrol")
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    result = orchestrate.run_step(desk)

    assert result.phase == "wait_plates"
    assert load_series(desk).look.status == "pending"


def test_an_adopted_desk_approved_by_fictora_ops_is_not_held(
    desk: Path, api: FakeApi
) -> None:
    _draw(desk, api, URL_1)
    approve_series_gate(desk, "look", path="shared/look/look-frame-v1.png")
    set_phase(desk, "ready_boards_enrol")

    assert orchestrate.look_gate_refusal(desk) is None
