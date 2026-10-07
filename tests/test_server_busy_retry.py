"""A busy server (502/503/504) is asked again when that is safe, and never fails a desk.

- Beach Court (L-20261005-21): an HTTP 502 while polling a film job marked the
  desk failed while the paid job kept running. Reads are now asked again with
  backoff; when the server stays busy the stop is a ``ServerBusy`` and ``step``
  leaves the desk where it was, so the same command picks the job up.
- Sighted ep4 / NOCLIP (L-20261005-18): the sound-effect route answered 502
  during ``finish`` and the cue was lost; a manual re-run minutes later worked.
  Every operator audio route replays its key (the cue is cached by its
  content), so a busy answer or a dropped connection is now asked again.
- A request that can start paid work (a film, plates, boards) is sent once:
  the server may already have started it. The stop says so; the re-run sends
  the same key and picks up the job.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import set_phase
from creation import orchestrate
from creation.harness.http_util import ConnectionDropped, ServerBusy
from creation.harness.session import DramaApiRunSession
from creation.post.audio_service import CUE_DEADLINE_SECONDS, post_with_retries
from creation.production_state import load_production
from fake_api import FakeApi

JOB = "/v1/jobs/job_video_1"


def _session(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    slept: list[float],
) -> DramaApiRunSession:
    run = DramaApiRunSession(
        base_url="https://drama.test",
        token="t",
        out_dir=tmp_path / "api",
        session_id="sess",
        sleep=slept.append,
    )
    run.client = httpx.Client(transport=httpx.MockTransport(handler))
    return run


def _answers(*answers: httpx.Response | Exception) -> tuple[Any, list[httpx.Request]]:
    queue = list(answers)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return handler, seen


def test_a_busy_read_is_asked_again_then_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    handler, seen = _answers(
        httpx.Response(502, text="Bad Gateway"),
        httpx.Response(503, json={"error": {"code": "unavailable"}}, headers={"Retry-After": "5"}),
        httpx.Response(200, json={"status": "running", "progress": 40}),
    )  # fmt: skip
    slept: list[float] = []

    job = _session(tmp_path, handler, slept).get(JOB)

    assert job["status"] == "running" and len(seen) == 3
    assert 7.5 <= slept[0] <= 12.5 and slept[1] == 5.0, (
        "jittered 10 s, then Retry-After"
    )
    err = capsys.readouterr().err
    assert "Server busy (HTTP 502), retrying in" in err and "(2/4)" in err
    assert "(3/4)" in err


def test_a_film_poll_the_server_stays_busy_on_keeps_the_desk_resumable(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    handler, seen = _answers(httpx.Response(502, text="Bad Gateway"))
    slept: list[float] = []
    real = _session(tmp_path, handler, slept)
    fake_get = api.get

    def get(path: str) -> dict[str, Any]:
        return real.get(path) if path.startswith("/v1/jobs/") else fake_get(path)

    api.get = get  # type: ignore[method-assign]
    set_phase(desk, "ready_video", video_enrolled_suffix="")
    (desk / "ep01" / "api" / "16_video_enrol.json").write_text(
        json.dumps({"job_id": "job_video_1"}), encoding="utf-8"
    )

    with pytest.raises(ServerBusy) as caught:
        orchestrate.run_step(desk, confirm_spend=True)

    assert isinstance(caught.value, ConnectionDropped)
    assert len(seen) == 4 and len(slept) == 3, "four tries, three waits"
    message = str(caught.value.code)
    assert "still running" in message and "Run the same command again" in message
    state = load_production(desk)
    assert state.phase == "ready_video" and state.failed_phase is None, (
        "a busy server never marks the desk failed while the paid job runs"
    )


def test_a_refusal_is_not_asked_again(tmp_path: Path) -> None:
    handler, seen = _answers(
        httpx.Response(409, json={"error": {"code": "stale_spine", "message": "no"}})
    )
    slept: list[float] = []

    with pytest.raises(SystemExit) as caught:
        _session(tmp_path, handler, slept).get(JOB)

    assert not isinstance(caught.value, ServerBusy)
    assert "stale_spine" in str(caught.value.code)
    assert len(seen) == 1 and slept == []


def test_a_request_that_starts_paid_work_is_sent_once(tmp_path: Path) -> None:
    handler, seen = _answers(httpx.Response(502, text="Bad Gateway"))
    slept: list[float] = []

    with pytest.raises(ServerBusy) as caught:
        _session(tmp_path, handler, slept).post(
            "/v1/video-generations",
            {"episode_ordinal": 1},
            idempotency_key="desk-video",
        )

    assert len(seen) == 1 and slept == [], "never sent again by the kit"
    message = str(caught.value.code)
    assert "did not send it again" in message and "same request key" in message


def test_hosted_post_off_is_not_an_outage(tmp_path: Path) -> None:
    handler, seen = _answers(
        httpx.Response(503, json={"error": {"code": "restate_unavailable"}})
    )
    slept: list[float] = []

    with pytest.raises(SystemExit) as caught:
        _session(tmp_path, handler, slept).get(
            "/v1/video-generations/job_video_1/post-production"
        )

    assert not isinstance(caught.value, ServerBusy)
    assert "finish" in str(caught.value.code) and len(seen) == 1


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.mark.parametrize(
    "first",
    [
        httpx.Response(502, text="<html>Bad Gateway</html>"),
        httpx.Response(503, json={"error": {"code": "unavailable"}}),
        httpx.ConnectError("connection reset"),
    ],
    ids=["gateway-502", "503", "dropped"],
)
def test_a_cue_the_server_was_busy_on_is_asked_again(
    first: httpx.Response | Exception,
) -> None:
    handler, seen = _answers(first, httpx.Response(200, json={"audio_url": "u"}))
    clock = _Clock()

    answer = post_with_retries(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "https://drama.test/v1/spines/sp/sfx-cues",
        {"Idempotency-Key": "cue-key"},
        {"sound": "door slam", "seconds": 1.0},
        sleep=clock.sleep,
        deadline_seconds=CUE_DEADLINE_SECONDS,
        clock=clock,
    )

    assert answer == {"audio_url": "u"}
    assert len(seen) == 2 and len(clock.slept) == 1
    assert {r.headers["Idempotency-Key"] for r in seen} == {"cue-key"}


class _PollClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_a_job_poll_rides_out_a_busy_server() -> None:
    from creation.harness.http_util import poll_until_terminal

    handler, seen = _answers(
        httpx.Response(504, text="Gateway Timeout"),
        httpx.Response(200, json={"status": "completed", "job_id": "job_boards"}),
    )

    job = poll_until_terminal(
        httpx.Client(transport=httpx.MockTransport(handler)),
        url=f"https://drama.test{JOB}",
        headers={},
        emit=None,
        label="boards",
        deadline_seconds=3600.0,
        clock=_PollClock(),
    )

    assert job["status"] == "completed" and len(seen) == 2


def test_a_busy_stop_keeps_the_servers_words(tmp_path: Path) -> None:
    from creation.harness_rules import thumbnail_answered_audio_error

    body = {
        "error": {
            "code": "operator_audio_failed",
            "message": "The audio could not be made",
        }
    }
    handler, seen = _answers(httpx.Response(502, json=body))

    with pytest.raises(ServerBusy) as caught:
        _session(tmp_path, handler, []).post(
            "/v1/spines/sp1/episodes/1/thumbnail",
            {"video_url": "u"},
            idempotency_key="k",
        )

    assert len(seen) == 1
    assert thumbnail_answered_audio_error(str(caught.value.code)), (
        "finish still skips the cover on this answer"
    )
