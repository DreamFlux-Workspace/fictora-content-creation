"""A stalled plan is read again before any re-draft: adopt it, wait on it, or re-draft (L-20260922-2)."""

from __future__ import annotations

from typing import Any

import pytest

from creation.harness import stages_gated

STALLED = {
    "status": "failed",
    "error": {
        "code": "authoring_stalled",
        "message": "stopped moving",
        "retryable": True,
    },
}
DONE = {"status": "completed", "progress": 100}


class _Run:
    prefix = "run"

    def __init__(self, plans: dict[str, list[dict[str, Any]]]) -> None:
        self.plans = plans
        self.posts: list[str] = []
        self.polled: list[str] = []
        self.saved: dict[str, Any] = {}
        self.events: list[str] = []

    def post(self, _path: str, _body: dict, *, idempotency_key: str) -> dict[str, Any]:
        self.posts.append(idempotency_key)
        n = len(self.posts)
        return {"plan_job_id": f"job_plan_{n}", "spine_id": f"spine_{n}"}

    def post_optional(
        self, path: str, body: dict, *, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        return 202, self.post(path, body, idempotency_key=idempotency_key)

    def poll_job(self, job_id: str, **_k: Any) -> dict[str, Any]:
        self.polled.append(job_id)
        return self.plans[job_id].pop(0)

    def spine(self, spine_id: str) -> dict[str, Any]:
        return {"spine_id": spine_id}

    def save(self, name: str, payload: Any) -> None:
        self.saved[name] = payload

    def emit(self, phase: str, **_k: Any) -> None:
        self.events.append(phase)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stages_gated.time, "sleep", lambda _s: None)


#: The key the draft of brief "p" is sent under: the run prefix, the brief's hash, ``-draft``.
KEY = "run-b{}-draft".format(
    stages_gated.draft_brief_hash(
        stages_gated.draft_request_body(prompt="p", preset_id="x", preset_version="1")
    )
)


def _draft(run: _Run) -> tuple[str, dict[str, Any]]:
    return stages_gated.start_draft(run, prompt="p", preset_id="x", preset_version="1")  # type: ignore[arg-type]


def test_a_stalled_plan_that_completed_after_all_is_adopted_not_paid_twice() -> None:
    run = _Run({"job_plan_1": [STALLED, DONE]})

    spine_id, plan = _draft(run)

    assert spine_id == "spine_1" and plan["status"] == "completed"
    assert run.posts == [KEY], "no second draft was enrolled"
    assert "plan_adopted" in run.events and "plan_retry" not in run.events
    assert run.saved["03_spine.json"] == {"spine_id": "spine_1"}


def test_a_stalled_plan_still_failed_on_the_second_read_is_re_drafted_once() -> None:
    run = _Run({"job_plan_1": [STALLED, STALLED], "job_plan_2": [DONE]})

    spine_id, _ = _draft(run)

    assert run.polled[:2] == ["job_plan_1", "job_plan_1"], (
        "the original job was read again first"
    )
    assert run.posts == [KEY, f"{KEY}-a1"]
    assert spine_id == "spine_2"


def test_another_failure_on_the_second_read_stops_without_a_re_draft() -> None:
    other = {
        "status": "failed",
        "error": {"code": "rule_failed", "message": "no", "retryable": False},
    }
    run = _Run({"job_plan_1": [STALLED, other]})

    with pytest.raises(SystemExit, match="rule_failed"):
        _draft(run)
    assert run.posts == [KEY]
