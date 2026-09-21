"""Tests for raw scene clip URL extraction."""

from __future__ import annotations

from creation.harness.raw_video import clip_url_from_job_payload


def test_clip_url_from_job_payload_reads_video_url() -> None:
    payload = {
        "result": {
            "video": {"url": "https://example.com/clip.mp4"},
        }
    }
    assert clip_url_from_job_payload(payload) == "https://example.com/clip.mp4"


def test_clip_url_from_job_payload_reads_fictora_media() -> None:
    payload = {
        "result": {
            "_fictora_media": {"public_url": "https://example.com/media.mp4"},
        }
    }
    assert clip_url_from_job_payload(payload) == "https://example.com/media.mp4"
