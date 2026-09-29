"""Generated-audio requests stop at a deadline with a safe retry hint; they never wait forever.

On 2026-09-29 ``fictora-produce cue`` ("a quick soft anime blush puff, a short
airy poof", 0.8 s) sat for more than ten minutes with no output; the same
request came back in seconds on retry.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from creation.harness.session import DramaApiRunSession
from creation.post import audio_service
from creation.post.audio_service import (
    CUE_DEADLINE_SECONDS,
    AudioServiceError,
    AudioServiceTimeout,
    post_with_retries,
)

URL = "https://drama.test/v1/spines/sp/sfx-cues"
BODY = {"sound": "a quick soft anime blush puff, a short airy poof", "seconds": 0.8}


class _Clock:
    """Monotonic time that moves only when the code under test sleeps or a request takes time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _err(status: int, code: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": "m"}}, headers=headers
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_endless_in_progress_stops_before_the_deadline_with_the_retry_hint() -> None:
    clock = _Clock()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        clock.now += 1.0
        return _err(409, "operator_audio_in_progress", **{"Retry-After": "15"})

    with pytest.raises(AudioServiceTimeout) as caught:
        post_with_retries(
            _client(handler), URL, {"Idempotency-Key": "same"}, BODY,
            sleep=clock.sleep, deadline_seconds=CUE_DEADLINE_SECONDS, clock=clock,
        )  # fmt: skip

    message = str(caught.value)
    assert "120 s" in message and "in progress" in message
    assert "same Idempotency-Key" in message and "Run the same command again" in message
    assert clock.now < CUE_DEADLINE_SECONDS, "never sleeps past the deadline"
    assert {r.headers["Idempotency-Key"] for r in seen} == {"same"}


def test_a_request_with_no_answer_is_a_timeout_bounded_by_what_is_left() -> None:
    clock = _Clock()
    clock.now = 100.0
    timeouts: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("no bytes", request=request)

    with pytest.raises(AudioServiceTimeout, match="got no answer"):
        post_with_retries(
            _client(handler), URL, {"Idempotency-Key": "k"}, BODY,
            sleep=clock.sleep, deadline_seconds=CUE_DEADLINE_SECONDS, clock=clock,
        )  # fmt: skip
    (timeout,) = timeouts
    assert timeout["read"] == pytest.approx(CUE_DEADLINE_SECONDS)
    # A timeout is still an AudioServiceError, so the CLI prints it instead of a traceback.
    assert issubclass(AudioServiceTimeout, AudioServiceError) and issubclass(
        AudioServiceError, RuntimeError
    )


def test_a_server_timeout_504_is_replayed_with_the_same_key_after_retry_after() -> None:
    clock = _Clock()
    answers = [_err(504, "operator_audio_timed_out", **{"Retry-After": "15"}),
               httpx.Response(200, json={"audio_url": "u"})]  # fmt: skip
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.pop(0)

    answer = post_with_retries(
        _client(handler), URL, {"Idempotency-Key": "same"}, BODY,
        sleep=clock.sleep, deadline_seconds=CUE_DEADLINE_SECONDS, clock=clock,
    )  # fmt: skip
    assert answer == {"audio_url": "u"}
    assert clock.slept == [15.0]
    assert [r.headers["Idempotency-Key"] for r in seen] == ["same", "same"]


def test_repeated_504s_stop_with_the_hint() -> None:
    clock = _Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        return _err(504, "operator_audio_timed_out", **{"Retry-After": "1"})

    with pytest.raises(AudioServiceError, match="same Idempotency-Key"):
        post_with_retries(
            _client(handler), URL, {"Idempotency-Key": "k"}, BODY,
            sleep=clock.sleep, deadline_seconds=CUE_DEADLINE_SECONDS, clock=clock,
        )  # fmt: skip


def test_the_cue_call_carries_the_cue_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation.post import desk as desk_mod

    clock = _Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.now += 1.0
        return _err(409, "operator_audio_in_progress", **{"Retry-After": "15"})

    def open_api(desk: Path, episode: int) -> DramaApiRunSession:
        run = DramaApiRunSession(
            base_url="https://drama.test", token="t", out_dir=tmp_path, session_id="s"
        )
        run.client = _client(handler)
        return run

    monkeypatch.setattr(desk_mod, "open_api", open_api)
    audio = audio_service.DramaApiAudio(tmp_path, sleep=clock.sleep, clock=clock)
    with pytest.raises(AudioServiceTimeout, match="within 120 s"):
        audio.sfx_cue(spine_id="sp", sound=BODY["sound"], seconds=0.8, key="k")
    assert clock.now < CUE_DEADLINE_SECONDS
