"""Poll scene child jobs for raw clip URLs without waiting on API post-production."""

from __future__ import annotations

import time
from typing import Any

from creation.harness.session import DramaApiRunSession


def clip_url_from_job_payload(job: dict[str, Any]) -> str | None:
    """Return a scene clip URL from one terminal ``GET /v1/jobs/{id}`` body."""

    result = job.get("result")
    if not isinstance(result, dict):
        return None
    video = result.get("video")
    if isinstance(video, dict):
        url = video.get("url")
        if url:
            return str(url)
    media = result.get("_fictora_media")
    if isinstance(media, dict):
        url = media.get("public_url")
        if url:
            return str(url)
    return None


def _job_record(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("job")
    if isinstance(nested, dict):
        return nested
    return payload


def wait_for_raw_scene_clips(
    run: DramaApiRunSession,
    coordinator_job_id: str,
    *,
    deadline_seconds: float = 7200.0,
    interval_seconds: float = 15.0,
) -> dict[str, Any]:
    """Poll until every scene child on the coordinator has a clip URL.

    Parameters
    ----------
    coordinator_job_id
        Parent ``job_video_*`` from ``16_video_enrol.json``.
    deadline_seconds
        Wall-clock cap for coordinator + child polling.

    Returns
    -------
    dict[str, Any]
        ``{"coordinator_job_id", "clips": [{"job_id", "url", "episode_id"?}]}``.
    """

    deadline = time.monotonic() + deadline_seconds
    child_ids: list[str] = []
    clips: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        parent = _job_record(run.get(f"/v1/jobs/{coordinator_job_id}"))
        depends = parent.get("depends_on")
        if isinstance(depends, list) and depends:
            child_ids = [str(item) for item in depends if item]
        if child_ids:
            pending = False
            clips = []
            for child_id in child_ids:
                child = _job_record(run.get(f"/v1/jobs/{child_id}"))
                status = str(child.get("status") or "")
                if status in {"queued", "running"}:
                    pending = True
                    continue
                if status == "failed":
                    raise SystemExit(f"scene job failed: {child_id} error={child.get('error')}")
                url = clip_url_from_job_payload(child)
                if url is None and status != "completed":
                    pending = True
                    continue
                if url is None:
                    pending = True
                    continue
                relation = child.get("relation") if isinstance(child.get("relation"), dict) else {}
                clips.append(
                    {
                        "job_id": child_id,
                        "url": url,
                        "relation_id": relation.get("id"),
                    }
                )
            if clips and not pending and len(clips) == len(child_ids):
                payload = {"coordinator_job_id": coordinator_job_id, "clips": clips}
                run.save("17_raw_scene_clips.json", payload)
                return payload
        run.emit(
            "poll_raw_scenes",
            status="running",
            progress=parent.get("progress"),
            job_id=coordinator_job_id,
            children=len(child_ids),
        )
        time.sleep(interval_seconds)

    raise SystemExit(f"timed out waiting for raw scene clips on {coordinator_job_id}")
