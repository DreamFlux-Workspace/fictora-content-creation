"""Note limits checked before sending, the stuck-job warning, and what ``cancel-job`` says.

Production, 2026-09-30: ``redraw-board --note`` sent a 500+ character note and the server
answered HTTP 422 (``note: String should have at most 500 characters``); then a redraw job sat
at ``running``, progress 0, with ``updated_at`` frozen for 20+ minutes while the poller printed
the same line, so stuck could not be told from slow.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from creation import episode_commands as ec
from creation.board_note import BOARD_NOTE_MAX, CAST_NOTE_MAX, check_note_length
from creation.harness import http_util
from creation.harness.session import DramaApiRunSession
from creation.recover import cancel_and_check
from fake_api import FakeApi
from test_redraw_board_note import _note_on_redraw_deploy, _record_digest, _regen_routes

REGEN = "/v1/spines/sp1/episodes/1/boards/1/regenerate"


# --- note limits ------------------------------------------------------------------------------------


def test_a_board_note_over_500_characters_is_refused_with_its_length_and_nothing_is_sent(
    desk: Path, api: FakeApi
) -> None:
    note = "x" * (BOARD_NOTE_MAX + 12)

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_redraw_board(desk, episode=1, take_id="t1", note=note)

    message = str(stopped.value)
    assert "512 characters" in message
    assert "at most 500" in message
    assert "Nothing was sent or booked" in message
    assert api.calls == []


def test_a_board_note_of_exactly_500_characters_goes_on_the_redraw(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _note_on_redraw_deploy(api)
    note = "y" * BOARD_NOTE_MAX

    ec.run_redraw_board(desk, episode=1, take_id="t1", note=note, out=io.StringIO())

    assert (api.posted(REGEN)[0] or {})["note"] == note


def test_the_count_collapses_whitespace_the_way_the_server_does() -> None:
    check_note_length("word   " * 80, limit=BOARD_NOTE_MAX, command="redraw-board")
    with pytest.raises(ValueError, match="501 characters"):
        check_note_length("z" * 501, limit=BOARD_NOTE_MAX, command="redraw-board")


def test_a_plate_note_over_the_cast_note_limit_is_refused_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(ec.CommandStopped, match=r"at most 2000"):
        ec.run_redraw_plate_with_note(desk, cast="Ren", note="n" * (CAST_NOTE_MAX + 1))

    assert api.calls == []


def test_help_states_the_limits(capsys: pytest.CaptureFixture[str]) -> None:
    from creation.cli_produce import main as produce_main

    for command, limit in (("redraw-board", "<=500"), ("redraw-plate", "<=2000")):
        with pytest.raises(SystemExit):
            produce_main([command, "--help"])
        assert limit in " ".join(capsys.readouterr().out.split())


# --- stuck-job warning ------------------------------------------------------------------------------


class FakeClock:
    """Monotonic time that only moves when the poll sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _client(payloads: list[dict[str, Any]]) -> Mock:
    answers = iter(payloads)

    def fake_get(url: str, headers: dict[str, str]) -> Mock:
        response = Mock()
        response.is_success = True
        response.json.return_value = next(answers)
        return response

    client = Mock()
    client.get = fake_get
    return client


def _poll(
    payloads: list[dict[str, Any]], *, interval: float = 60.0, deadline: float = 7200.0
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    result = http_util.poll_until_terminal(
        _client(payloads),
        url="https://example.test/v1/video-generations/job_x",
        headers={},
        emit=None,
        label="boards",
        deadline_seconds=deadline,
        interval_seconds=interval,
        job_id="job_x",
        desk="/desks/show",
        warn=warnings.append,
        clock=FakeClock(),
        utc_now=lambda: datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
    )
    return result, warnings


FROZEN = {"status": "running", "progress": 0, "updated_at": "2026-09-30T08:41:00Z"}


def test_a_job_frozen_for_ten_minutes_warns_once_then_every_ten_minutes() -> None:
    # One poll a minute: polls 0..25 frozen (25 min), then completed.
    payloads = [dict(FROZEN) for _ in range(26)] + [{"status": "completed"}]

    result, warnings = _poll(payloads)

    assert result["status"] == "completed"
    assert len(warnings) == 2
    assert warnings[0] == (
        "The server has not updated this job for 10 min (last update 08:41 UTC). It may be stuck: "
        "check `fictora-produce status`; stop it with "
        "`fictora-produce cancel-job --desk /desks/show --job-id job_x`."
    )
    assert "for 20 min" in warnings[1]


def test_a_job_whose_updated_at_moves_never_warns() -> None:
    payloads = [
        {
            "status": "running",
            "progress": 0,
            "updated_at": f"2026-09-30T08:{minute:02d}:00Z",
        }
        for minute in range(30)
    ] + [{"status": "completed"}]

    _, warnings = _poll(payloads)

    assert warnings == []


def test_progress_moving_resets_the_clock_even_without_updated_at() -> None:
    frozen = [{"status": "running", "progress": 0} for _ in range(9)]
    moved = [{"status": "running", "progress": 5} for _ in range(9)]

    _, warnings = _poll([*frozen, *moved, {"status": "completed"}])

    assert warnings == []


def test_without_updated_at_the_warning_names_when_the_kit_last_saw_a_change() -> None:
    payloads = [{"status": "running", "progress": 0} for _ in range(11)] + [
        {"status": "completed"}
    ]

    _, warnings = _poll(payloads)

    assert len(warnings) == 1
    assert "(last update 09:00 UTC)" in warnings[0]


def test_the_deadline_still_holds_and_nothing_is_cancelled() -> None:
    payloads = [dict(FROZEN) for _ in range(100)]
    calls: list[str] = []
    client = _client(payloads)
    client.post = Mock(side_effect=AssertionError("the poll never cancels"))

    with pytest.raises(SystemExit, match="timed out polling boards after 1800s"):
        http_util.poll_until_terminal(
            client,
            url="https://example.test/v1/jobs/job_x",
            headers={},
            emit=None,
            label="boards",
            deadline_seconds=1800.0,
            interval_seconds=60.0,
            warn=calls.append,
            clock=FakeClock(),
        )
    assert len(calls) == 2


def test_the_session_names_its_desk_in_the_cancel_command(tmp_path: Path) -> None:
    run = DramaApiRunSession(
        base_url="https://example.test", token="t", out_dir=tmp_path / "ep02" / "api"
    )
    other = DramaApiRunSession(
        base_url="https://example.test", token="t", out_dir=tmp_path / "scratch"
    )
    try:
        assert run.desk_hint() == str(tmp_path)
        assert other.desk_hint() is None
    finally:
        run.client.close()
        other.client.close()


# --- cancel-job -------------------------------------------------------------------------------------


def _cancel_api(tmp_path: Path, answer: dict[str, Any], after: Any = None) -> FakeApi:
    api = FakeApi(tmp_path / "api")
    api.routes[("POST", "/v1/jobs/job_x/cancel")] = answer
    if after is not None:
        api.routes[("GET", "/v1/jobs/job_x")] = after
    return api


def test_a_cancel_that_has_not_stopped_the_job_says_so_waits_and_reads_it_once_more(
    tmp_path: Path,
) -> None:
    api = _cancel_api(
        tmp_path,
        {
            "terminal": False,
            "cancellation_requested": True,
            "job": {"status": "running"},
        },
        after={"status": "cancelled"},
    )
    slept: list[float] = []

    _, lines = cancel_and_check(api, "job_x", wait_seconds=15.0, sleep=slept.append)

    text = "\n".join(lines)
    assert (
        "Cancel requested, but job job_x has not stopped yet (status: running)" in text
    )
    assert "may keep running" in text and "server fix is in progress" in text
    assert "The job has stopped (status: cancelled)." in text
    assert slept == [15.0]
    assert [(m, p) for m, p, _, _ in api.calls] == [
        ("POST", "/v1/jobs/job_x/cancel"),
        ("GET", "/v1/jobs/job_x"),
    ]


def test_a_job_still_running_after_the_wait_is_reported_and_not_cancelled_again(
    tmp_path: Path,
) -> None:
    api = _cancel_api(
        tmp_path,
        {"terminal": False, "job": {"status": "running"}},
        after={"status": "running"},
    )

    _, lines = cancel_and_check(api, "job_x", sleep=lambda _: None)

    assert any("The job is still running" in line for line in lines)
    assert len(api.posted("/v1/jobs/job_x/cancel")) == 1


def test_a_cancel_that_stopped_the_job_reads_nothing_more(tmp_path: Path) -> None:
    api = _cancel_api(tmp_path, {"terminal": True, "job": {"status": "cancelled"}})
    slept: list[float] = []

    _, lines = cancel_and_check(api, "job_x", sleep=slept.append)

    assert lines[0] == "Cancelled (job status: cancelled)."
    assert slept == []
    assert len(api.calls) == 1


def test_cancel_job_cli_prints_the_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation import cli_produce

    monkeypatch.setattr(
        cli_produce,
        "cancel_video_job",
        lambda desk, job_id: ({"terminal": False}, ["Cancel requested, but ..."]),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli_produce.main(
            ["cancel-job", "--desk", str(tmp_path), "--job-id", "job_x"]
        )
    assert code == 0
    assert "Cancel requested, but ..." in out.getvalue()
    assert "Cancelled. Do not enrol" not in out.getvalue()
