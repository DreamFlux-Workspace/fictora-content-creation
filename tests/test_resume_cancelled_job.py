"""A saved job that was cancelled or failed is never picked up again (SCP Blink #89).

``redraw-board`` stopped on its first re-run after ``cancel-job``: ``run_unit`` reused the
saved job id whatever its status, polled the cancelled job and stopped. A dead saved job now
starts over under the next attempt's key (and says so); a running or completed one is
picked up as before and never re-sent.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from creation import episode_commands as ec
from creation.production_state import load_production, save_production
from fake_api import FakeApi
from test_redraw_board_note import (
    NOTE,
    REGEN,
    _note_on_redraw_deploy,
    _record_digest,
    _regen_routes,
)

UNIT = "boards-ep01-t1-redraw"
OLD = "/v1/video-generations/job_old"


def _saved(desk: Path, job_id: str = "job_old") -> None:
    state = load_production(desk)
    state.pending[UNIT] = {"key": f"pfx-{UNIT}-a1", "job_id": job_id}
    save_production(desk, state)


def _saved_writing(desk: Path) -> None:
    state = load_production(desk)
    state.pending["author-ep02"] = {"key": "pfx-author-ep02-a1", "job_id": "job_1"}
    save_production(desk, state)


def _keys(api: FakeApi) -> list[str | None]:
    return [k for m, p, _, k in api.calls if m == "POST" and p == REGEN]


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_a_cancelled_or_failed_saved_redraw_starts_fresh_with_its_note(
    desk: Path, api: FakeApi, status: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _note_on_redraw_deploy(api)
    _saved(desk)
    api.routes[("GET", OLD)] = {"id": "job_old", "status": status}
    api.jobs["job_old"] = {"status": status}

    ec.run_redraw_board(desk, episode=1, take_id="t1", note=NOTE, out=io.StringIO())

    assert _keys(api) == [f"{load_production(desk).idempotency_prefix}-{UNIT}-a2"]
    assert (api.posted(REGEN)[0] or {})["note"] == NOTE
    assert ("job_old", True) not in api.polled
    assert f"ended {status}" in capsys.readouterr().err
    state = load_production(desk)
    assert UNIT not in state.pending and state.attempts[UNIT] == 2


@pytest.mark.parametrize("status", ["running", "queued", "completed"])
def test_a_live_or_completed_saved_job_is_picked_up_and_never_resent(
    desk: Path, api: FakeApi, status: str
) -> None:
    _saved_writing(desk)
    api.routes[("GET", "/v1/jobs/job_1")] = {"status": status}
    api.jobs["job_1"] = {"status": "completed"}

    ec.run_unit(
        desk,
        api,  # type: ignore[arg-type]
        unit="author-ep02",
        path="/v1/x",
        body={},
        video_route=False,
        deadline_seconds=1,
    )

    assert api.posted("/v1/x") == [] and api.polled == [("job_1", False)]


def test_run_unit_restarts_a_cancelled_writing_job_under_the_next_key(
    desk: Path, api: FakeApi
) -> None:
    _saved_writing(desk)
    api.routes[("GET", "/v1/jobs/job_1")] = {"status": "cancelled"}
    api.routes[("POST", "/v1/x")] = {"job_id": "job_2"}
    api.jobs["job_2"] = {"status": "completed"}

    ec.run_unit(
        desk,
        api,  # type: ignore[arg-type]
        unit="author-ep02",
        path="/v1/x",
        body={},
        video_route=False,
        deadline_seconds=1,
    )

    assert [k for m, p, _, k in api.calls if m == "POST"] == [
        f"{load_production(desk).idempotency_prefix}-author-ep02-a2"
    ]
    assert api.polled == [("job_2", False)]
