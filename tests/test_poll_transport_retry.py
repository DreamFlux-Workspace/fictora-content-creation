"""Poll loop resilience and delivery URL parsing."""

from __future__ import annotations

from unittest.mock import Mock

import httpx

from creation.harness import http_util
from creation.orchestrate import _delivery_video_url


def test_poll_until_terminal_retries_transient_read_error() -> None:
    calls = {"n": 0}

    def fake_get(url: str, headers: dict[str, str]) -> Mock:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("error reading a body from connection")
        response = Mock()
        response.is_success = True
        response.json.return_value = {"status": "completed", "job_id": "job_test"}
        return response

    client = Mock()
    client.get = fake_get

    payload = http_util.poll_until_terminal(
        client,
        url="https://example.test/v1/video-generations/job_test",
        headers={"Authorization": "Bearer x"},
        emit=None,
        label="video",
        deadline_seconds=60.0,
        interval_seconds=0.01,
    )

    assert payload["status"] == "completed"
    assert calls["n"] == 2


def test_delivery_video_url_reads_deliveries_array() -> None:
    delivery = {
        "deliveries": [
            {
                "video_url": "https://cdn.example/episode-captions.mp4",
                "caption_track_url": "https://cdn.example/track.vtt",
            }
        ]
    }
    assert _delivery_video_url(delivery) == "https://cdn.example/episode-captions.mp4"
