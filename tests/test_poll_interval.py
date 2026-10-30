"""The kit notices a finished step within seconds (generation speed plan item 04, 30 Oct 2026).

It read a running job every 15 s, so a finished step sat unseen for 7.5 s on
average before the kit moved on. It now reads a job every 3 s and a film every
5 s, still slower than the app, which reads the same routes every 2 s.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from creation.harness import raw_video, session
from creation.harness.session import JOB_POLL_SECONDS, DramaApiRunSession


def test_a_job_is_read_every_three_seconds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_poll(_client: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(session, "poll_until_terminal", fake_poll)
    run = DramaApiRunSession(base_url="https://drama.example", token="t", out_dir=tmp_path)
    try:
        run.poll_job("job_1", label="plan", video_route=False, deadline_seconds=60)
    finally:
        run.client.close()

    assert seen["interval_seconds"] == JOB_POLL_SECONDS
    assert JOB_POLL_SECONDS <= 3.0


def test_a_film_is_read_every_five_seconds() -> None:
    default = inspect.signature(raw_video.wait_for_raw_scene_clips).parameters["interval_seconds"].default
    assert default == raw_video.FILM_POLL_SECONDS
    assert raw_video.FILM_POLL_SECONDS <= 5.0
