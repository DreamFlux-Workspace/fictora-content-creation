"""Paid ``step`` stages on new desks: price first, the same key after a lost reply, no failed desk on a drop.

- The Gallery Heiress (L-20261006-17): a chained command drew plates before the
  producer's yes. Plates and boards now show their price on a bare ``step`` and
  draw only on ``step --confirm-spend``, like ``film``.
- Every run used a random key prefix, so a re-run after a lost reply could
  start a second paid job. ``step`` now sends its paid stages under the desk's
  fixed prefix; the server answers a repeated key with the job it already has.
- A dropped connection while waiting on a job marked the desk failed, leaving
  only paid restarts (L-20261006-14). It now leaves the desk where it was.

Desks created before 6 Oct 2026 keep their old behaviour (rules epoch).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import orchestrate
from creation.harness.http_util import ConnectionDropped
from creation.production_state import load_production
from creation.rules_epoch import run_rules_epoch
from fake_api import FakeApi

CAST = "/v1/spines/sp1/cast/enrol"
BOARDS = "/v1/spines/sp1/boards/enrol"


def _legacy(desk: Path) -> Path:
    run_rules_epoch(desk, set_to="legacy", out=io.StringIO())
    return desk


def _cast_draws(api: FakeApi) -> None:
    api.routes[("POST", CAST)] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}


def _boards_draw(api: FakeApi) -> None:
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {"boards": []}


def _keys(api: FakeApi, path: str) -> list[str | None]:
    return [
        key
        for method, called, _, key in api.calls
        if (method, called) == ("POST", path)
    ]


# --- price first (#4) ------------------------------------------------------------------------------


def test_a_bare_step_prices_the_plates_and_draws_nothing(
    desk: Path, api: FakeApi
) -> None:
    _cast_draws(api)
    set_phase(desk, "ready_cast_enrol")

    result = orchestrate.run_step(desk)

    assert "about $" in result.message and "--confirm-spend" in result.message
    assert "Nothing drawn yet" in result.message
    assert not api.posted(CAST)
    state = load_production(desk)
    assert (
        state.phase == "ready_cast_enrol" and "plates-ep01" in state.drawing_estimates
    )

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert result.phase == "wait_plates"
    assert api.posted(CAST)
    assert load_production(desk).drawing_estimates == {}


def test_confirming_plates_before_any_price_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    _cast_draws(api)
    set_phase(desk, "ready_cast_enrol")

    with pytest.raises(RuntimeError, match="no price shown for the plates"):
        orchestrate.run_step(desk, confirm_spend=True)

    assert not api.posted(CAST)
    assert load_production(desk).phase == "ready_cast_enrol"


def test_boards_are_priced_one_a_take(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", BOARDS)] = {"job_id": "job_boards"}
    _boards_draw(api)
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert "storyboard(s), one a take" in result.message and "about $" in result.message
    assert not api.posted(BOARDS)


def test_a_legacy_desk_still_draws_plates_on_a_bare_step(
    desk: Path, api: FakeApi
) -> None:
    _legacy(desk)
    _cast_draws(api)
    set_phase(desk, "ready_cast_enrol")

    result = orchestrate.run_step(desk)

    assert result.phase == "wait_plates"
    assert api.posted(CAST)


# --- the same key after a lost reply (#2) -----------------------------------------------------------


def _lost_reply_then_job(api: FakeApi) -> None:
    answers = iter([httpx.ConnectError("the reply was lost"), {"job_id": "job_boards"}])

    def post(*_: Any) -> dict[str, Any]:
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    api.routes[("POST", BOARDS)] = post
    _boards_draw(api)


def test_a_rerun_after_a_lost_reply_sends_the_same_boards_key(
    desk: Path, api: FakeApi
) -> None:
    _lost_reply_then_job(api)
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)

    with pytest.raises(httpx.ConnectError):
        orchestrate.run_step(desk, confirm_spend=True)
    assert load_production(desk).phase == "ready_boards_enrol"

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert result.phase == "wait_board"
    first, second = _keys(api, BOARDS)
    assert first == second
    assert first is not None and first.startswith(
        f"{load_production(desk).idempotency_prefix}-step-"
    )


def test_a_legacy_desk_keeps_the_run_prefix(desk: Path, api: FakeApi) -> None:
    _legacy(desk)
    _lost_reply_then_job(api)
    set_phase(desk, "ready_boards_enrol")

    with pytest.raises(httpx.ConnectError):
        orchestrate.run_step(desk)

    [key] = _keys(api, BOARDS)
    assert key is not None and key.startswith(f"{api.prefix}-")


# --- a drop while waiting does not fail the desk (#3) ----------------------------------------------


def _dropped_while_waiting(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    api.routes[("POST", BOARDS)] = {"job_id": "job_boards"}

    def poll_job(*_: Any, **__: Any) -> dict[str, Any]:
        raise ConnectionDropped("The connection dropped while waiting on the boards.")

    monkeypatch.setattr(api, "poll_job", poll_job)


def test_a_drop_while_waiting_leaves_the_desk_where_it_was(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dropped_while_waiting(api, monkeypatch)
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)

    with pytest.raises(ConnectionDropped):
        orchestrate.run_step(desk, confirm_spend=True)

    state = load_production(desk)
    assert state.phase == "ready_boards_enrol" and state.failed_phase is None


def test_a_legacy_desk_is_still_marked_failed_on_a_drop(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy(desk)
    _dropped_while_waiting(api, monkeypatch)
    set_phase(desk, "ready_boards_enrol")

    with pytest.raises(ConnectionDropped):
        orchestrate.run_step(desk)

    assert load_production(desk).phase == "failed"
