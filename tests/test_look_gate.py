"""The look gate: ``approve --gate look`` records and pins; ``step`` holds paid drawings for a drawn, unapproved look."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from creation import episode_commands as ec
from creation import orchestrate
from creation.cli_ops import main as ops_main
from creation.cli_produce import main as produce_main
from creation.ops.floor import approve_series_gate
from creation.ops.state import load_series
from creation.production_state import load_production
from conftest import SHOWN_PRICES, set_phase
from fake_api import FakeApi

FRAME_ROUTE = "/v1/spines/sp1/look-frame"
PIN_ROUTE = "/v1/spines/sp1/look-register"
URL_1 = "https://cdn.example/look-frame/v1.png"
URL_2 = "https://cdn.example/look-frame/v2.png"
URL_3 = "https://cdn.example/look-frame/v3.png"


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


def _draw_more(desk: Path, api: FakeApi, url: str) -> Path:
    api.routes[("POST", FRAME_ROUTE)] = _answer(url)
    return ec.run_look_frame(desk, description=url, out=io.StringIO())


def _written_at(frame: Path, at_utc: str | None, seconds: int) -> None:
    """Set the frame file's time to ``seconds`` after (or before) the look yes."""

    assert at_utc
    stamp = datetime.fromisoformat(at_utc).timestamp() + seconds
    os.utime(frame, (stamp, stamp))


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
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert result.phase == "wait_plates"
    assert api.posted("/v1/spines/sp1/cast/enrol")


def test_a_desk_that_never_drew_a_look_frame_is_not_held(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert result.phase == "wait_plates"
    assert load_series(desk).look.status == "pending"


def test_an_adopted_desk_approved_by_fictora_ops_is_not_held(
    desk: Path, api: FakeApi
) -> None:
    _draw(desk, api, URL_1)
    approve_series_gate(desk, "look", path="shared/look/look-frame-v1.png")
    set_phase(desk, "ready_boards_enrol")

    assert orchestrate.look_gate_refusal(desk) is None


# --- a new look frame after the yes ----------------------------------------------------------


def test_a_frame_drawn_after_the_yes_opens_the_gate_again(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _draw(desk, api, URL_1)
    _pin_answers(api)
    ec.run_approve_look(desk, out=io.StringIO())
    _draw_more(desk, api, URL_2)
    api.calls.clear()
    set_phase(desk, "ready_boards_enrol")

    assert produce_main(["step", "--desk", str(desk)]) == 2

    err = capsys.readouterr().err
    assert "look gate is open again" in err
    assert "shared/look/look-frame-v2.png was drawn after the look was approved" in err
    assert "shared/look/look-frame-v1.png" in err
    assert api.calls == []


def test_a_cached_redraw_of_the_approved_frame_keeps_the_gate_shut(
    desk: Path, api: FakeApi
) -> None:
    _draw(desk, api, URL_1)
    _pin_answers(api)
    ec.run_approve_look(desk, out=io.StringIO())

    _draw_more(desk, api, URL_1)

    assert orchestrate.look_gate_refusal(desk) is None


def test_picking_an_older_frame_covers_the_frames_already_drawn(
    desk: Path, api: FakeApi
) -> None:
    first, _second = _draw(desk, api, URL_1, URL_2)
    _pin_answers(api)
    ec.run_approve_look(desk, path=first, out=io.StringIO())

    assert orchestrate.look_gate_refusal(desk) is None
    stamp = json.loads((desk / "shared" / "look" / "look-approval.json").read_text())
    assert stamp["frame"] == "look-frame-v1.png"
    assert stamp["url"] == URL_1
    assert stamp["drawn"] == ["look-frame-v1.png", "look-frame-v2.png"]
    assert stamp["at_utc"] == load_series(desk).look.at_utc


def test_a_new_yes_closes_the_reopened_gate_even_for_the_same_frame(
    desk: Path, api: FakeApi
) -> None:
    (first,) = _draw(desk, api, URL_1)
    _pin_answers(api)
    ec.run_approve_look(desk, out=io.StringIO())
    _draw_more(desk, api, URL_2)
    assert orchestrate.look_gate_refusal(desk) is not None

    out = io.StringIO()
    ec.run_approve_look(desk, path=first, out=out)

    assert "already approved" not in out.getvalue()
    assert orchestrate.look_gate_refusal(desk) is None
    assert len(api.posted(PIN_ROUTE)) == 1


def test_an_older_yes_with_no_record_covers_frames_written_by_then(
    desk: Path, api: FakeApi
) -> None:
    (first,) = _draw(desk, api, URL_1)
    look = approve_series_gate(desk, "look")
    (desk / "shared" / "look" / "look-approval.json").unlink()
    _written_at(first, look.at_utc, -3600)
    assert orchestrate.look_gate_refusal(desk) is None

    second = _draw_more(desk, api, URL_2)
    _written_at(second, look.at_utc, -60)
    assert orchestrate.look_gate_refusal(desk) is None

    third = _draw_more(desk, api, URL_3)
    _written_at(third, look.at_utc, 60)
    refusal = orchestrate.look_gate_refusal(desk)
    assert (
        refusal and "look-frame-v3.png was drawn after the look was approved" in refusal
    )


def test_an_older_yes_naming_a_frame_covers_it_whenever_it_was_written(
    desk: Path, api: FakeApi
) -> None:
    (first,) = _draw(desk, api, URL_1)
    look = approve_series_gate(desk, "look", path="shared/look/look-frame-v1.png")
    (desk / "shared" / "look" / "look-approval.json").unlink()

    _written_at(first, look.at_utc, 3600)

    assert orchestrate.look_gate_refusal(desk) is None


def test_fictora_ops_look_yes_covers_the_frames_drawn_so_far_and_no_later_one(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _draw(desk, api, URL_1, URL_2)
    argv = ["approve", "--desk", str(desk), "--gate", "look"]
    assert ops_main(argv + ["--path", "look-frame-v1.png"]) == 0
    assert orchestrate.look_gate_refusal(desk) is None

    _draw_more(desk, api, URL_3)

    refusal = orchestrate.look_gate_refusal(desk)
    assert refusal and "shared/look/look-frame-v3.png" in refusal


# --- redraw-plate and redraw-board hold like step --------------------------------------------

REDRAWS = {
    "redraw-plate": ["--cast", "Ren", "--note", "shorter hair"],
    "redraw-board": ["--episode", "1", "--take", "t1", "--cause", "wrong room"],
}


@pytest.mark.parametrize("command", sorted(REDRAWS))
def test_a_redraw_is_refused_while_a_drawn_look_is_unapproved(
    desk: Path, api: FakeApi, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _draw(desk, api, URL_1)
    api.calls.clear()

    assert produce_main([command, "--desk", str(desk), *REDRAWS[command]]) == 2

    err = capsys.readouterr().err
    assert err.startswith("Refused: the look gate is open.")
    assert "shared/look/look-frame-v1.png" in err and "--gate look" in err
    assert f"Then run {command} again." in err
    assert api.calls == []


@pytest.mark.parametrize("command", sorted(REDRAWS))
def test_a_redraw_is_refused_for_a_frame_drawn_after_the_yes(
    desk: Path, api: FakeApi, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _draw(desk, api, URL_1)
    _pin_answers(api)
    ec.run_approve_look(desk, out=io.StringIO())
    _draw_more(desk, api, URL_2)
    api.calls.clear()
    capsys.readouterr()

    assert produce_main([command, "--desk", str(desk), *REDRAWS[command]]) == 2

    assert "look gate is open again" in capsys.readouterr().err
    assert api.calls == []


@pytest.mark.parametrize("command", sorted(REDRAWS))
def test_a_redraw_passes_the_gate_once_the_look_is_approved(
    desk: Path, api: FakeApi, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _draw(desk, api, URL_1)
    _pin_answers(api)
    ec.run_approve_look(desk, out=io.StringIO())
    capsys.readouterr()

    api.calls.clear()

    # Past the gate, the command reaches the paid route this fake does not serve.
    with pytest.raises(AssertionError, match="unexpected POST"):
        produce_main([command, "--desk", str(desk), *REDRAWS[command]])

    assert "look gate" not in capsys.readouterr().err
    assert _paid_calls(api)
