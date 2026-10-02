"""Re-running ``step`` after a dead session never sends a second paid draft (L-20260926-4)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.production_state import load_production
from creation.recover import retry_failed_step
from fake_api import FakeApi

DRAFTS = "/v1/prompt-video-authoring-drafts"


class SessionDied(BaseException):
    """The terminal closed (or the laptop slept) while the plan job was polled."""


@pytest.fixture
def fresh_session_per_run(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> FakeApi:
    """Each ``step`` opens its own session with its own random prefix, as the real kit does."""

    def open_run(_desk: Path, _state: object) -> FakeApi:
        api.prefix = f"create-flow-{uuid.uuid4().hex[:8]}"
        return api

    monkeypatch.setattr(orchestrate, "_open_run", open_run)
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", DRAFTS)] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    set_phase(desk, "new", spine_id=None)
    return api


def _draft_keys(api: FakeApi) -> list[str | None]:
    return [
        key for method, path, _, key in api.calls if (method, path) == ("POST", DRAFTS)
    ]


def _die_on_first_poll(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    real = api.poll_job
    polls = {"n": 0}

    def poll(job_id: str, **kwargs: object) -> dict:
        polls["n"] += 1
        if polls["n"] == 1:
            raise SessionDied
        return real(job_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(api, "poll_job", poll)


def test_two_steps_after_a_dead_session_send_one_draft(
    desk: Path, fresh_session_per_run: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = fresh_session_per_run
    api.jobs["job_plan"] = {"status": "completed"}
    _die_on_first_poll(api, monkeypatch)

    with pytest.raises(SessionDied):
        orchestrate.run_step(desk)
    assert load_production(desk).phase == "new"

    orchestrate.run_step(desk)

    assert len(_draft_keys(api)) == 1, "the second step adopted the accepted draft"
    assert [job for job, _ in api.polled] == ["job_plan"]
    assert ("draft_adopted", "job_plan") in [
        (phase, payload.get("job_id")) for phase, payload in api.events
    ]
    state = load_production(desk)
    assert state.phase == "ready_cast_enrol" and state.spine_id == "sp1"
    assert orchestrate.draft_unit(1) not in state.pending


def test_a_session_that_died_before_the_answer_resends_under_the_same_key(
    desk: Path, fresh_session_per_run: FakeApi
) -> None:
    api = fresh_session_per_run
    api.jobs["job_plan"] = {"status": "completed"}
    answers = iter([SessionDied, {"plan_job_id": "job_plan", "spine_id": "sp1"}])

    def draft(_m: str, _p: str, _b: object) -> dict:
        answer = next(answers)
        if answer is SessionDied:
            raise SessionDied
        return answer  # type: ignore[return-value]

    api.routes[("POST", DRAFTS)] = draft

    with pytest.raises(SessionDied):
        orchestrate.run_step(desk)
    orchestrate.run_step(desk)

    keys = _draft_keys(api)
    assert len(keys) == 2 and keys[0] == keys[1], (
        "the server sees one key and hands back the draft it already took"
    )
    prefix = load_production(desk).idempotency_prefix
    assert re.fullmatch(rf"{re.escape(prefix)}-ep01-b[0-9a-f]{{10}}-draft", keys[0])


def test_a_deliberate_re_draft_after_retry_step_gets_a_new_key(
    desk: Path, fresh_session_per_run: FakeApi
) -> None:
    api = fresh_session_per_run
    api.jobs["job_plan"] = {
        "status": "failed",
        "error": {"code": "rule_failed", "message": "no", "retryable": False},
    }
    with pytest.raises(SystemExit):
        orchestrate.run_step(desk)

    retry_failed_step(desk, cause="brief edited")
    api.jobs["job_plan"] = {"status": "completed"}
    orchestrate.run_step(desk)

    first, second = _draft_keys(api)
    assert first != second
    assert re.search(r"-step-retry-ep01-new-r1-b[0-9a-f]{10}-draft$", second)
