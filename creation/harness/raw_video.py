"""Poll the take jobs of a video generation for their raw clips; post-production happens locally.

The coordinator returned by ``POST /v1/video-generations`` lists the jobs that
film each take in ``depends_on``. Each child says which episode it filmed
(``episode_ids``) and which board it filmed from (``relation.id`` ending
``_setNN``). The take's facts (lane, length, reference images, shot windows,
which approved line ids its instructions carry) are read from
``GET /v1/jobs/{take_job_id}/take-facts``; the compiled prompt stays on the
server and is never fetched.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

from creation.harness.http_util import describe_job_error
from creation.harness.session import DramaApiRunSession

_SET_SUFFIX = re.compile(r"_set(\d+)$")
_FAILED = frozenset({"failed", "cancelled"})


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


def set_index_of(relation_id: str | None) -> int | None:
    """Return the 1-based board (take) index from a child's ``relation.id`` (``..._set02`` -> 2)."""

    match = _SET_SUFFIX.search(str(relation_id or ""))
    return int(match.group(1)) if match else None


def wait_for_raw_scene_clips(
    run: DramaApiRunSession,
    coordinator_job_id: str,
    *,
    deadline_seconds: float = 7200.0,
    interval_seconds: float = 15.0,
    save_as: str = "17_raw_scene_clips.json",
) -> dict[str, Any]:
    """Poll until every take job on the coordinator has a clip URL.

    Stops loud, with the server's code and rule, when the coordinator or a take
    job fails: a take the server refuses (for example one that would drop an
    approved line) fails before any child is filmed.

    Parameters
    ----------
    run
        Active harness session.
    coordinator_job_id
        Parent ``job_video_*`` from ``16_video_enrol.json``.
    deadline_seconds
        Wall-clock cap for coordinator + child polling.
    interval_seconds
        Sleep between polls.
    save_as
        Artefact name for the clip list (a ``film`` re-film keeps its own, never the step's).

    Returns
    -------
    dict[str, Any]
        ``{"coordinator_job_id", "clips": [{"job_id", "url", "relation_id", "set_index", "episode_id"}]}``.

    Raises
    ------
    SystemExit
        When the coordinator or a take job failed, or at the deadline.
    """

    deadline = time.monotonic() + deadline_seconds
    child_ids: list[str] = []
    clips: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        parent = _job_record(run.get(f"/v1/jobs/{coordinator_job_id}"))
        if str(parent.get("status") or "") in _FAILED:
            run.save("17_video_terminal.json", parent)
            raise SystemExit(f"video job {coordinator_job_id} {describe_job_error(parent)}")
        depends = parent.get("depends_on")
        if isinstance(depends, list) and depends:
            child_ids = [str(item) for item in depends if item]
        if child_ids:
            pending = False
            clips = []
            for child_id in child_ids:
                child = _job_record(run.get(f"/v1/jobs/{child_id}"))
                status = str(child.get("status") or "")
                if status in {"queued", "running", ""}:
                    pending = True
                    continue
                if status in _FAILED:
                    raise SystemExit(f"take job {child_id} {describe_job_error(child)}")
                url = clip_url_from_job_payload(child)
                if url is None:
                    pending = True
                    continue
                relation = child.get("relation") if isinstance(child.get("relation"), dict) else {}
                episodes = child.get("episode_ids") or []
                clips.append(
                    {
                        "job_id": child_id,
                        "url": url,
                        "relation_id": relation.get("id"),
                        "set_index": set_index_of(relation.get("id")),
                        "episode_id": str(episodes[0]) if episodes else "",
                    }
                )
            if clips and not pending and len(clips) == len(child_ids):
                payload = {"coordinator_job_id": coordinator_job_id, "clips": clips}
                run.save(save_as, payload)
                return payload
        elif str(parent.get("status") or "") == "completed":
            raise SystemExit(f"video job {coordinator_job_id} completed with no take jobs in depends_on")
        run.emit(
            "poll_raw_scenes",
            status="running",
            progress=parent.get("progress"),
            job_id=coordinator_job_id,
            children=len(child_ids),
        )
        time.sleep(interval_seconds)

    raise SystemExit(f"timed out waiting for raw scene clips on {coordinator_job_id}")


def episode_clips(raw: dict[str, Any], *, episode_id: str) -> list[dict[str, Any]]:
    """Return one episode's clips in take order (by board index, then output order).

    A clip whose child did not name its episode is kept (older answers carried no ``episode_ids``).

    Parameters
    ----------
    raw
        :func:`wait_for_raw_scene_clips` result.
    episode_id
        The episode's API id.

    Returns
    -------
    list[dict[str, Any]]
        Clips for that episode.
    """

    mine = [clip for clip in raw.get("clips") or [] if clip.get("episode_id") in ("", episode_id)]
    ordered = sorted(enumerate(mine), key=lambda pair: (pair[1].get("set_index") or pair[0] + 1, pair[0]))
    return [clip for _, clip in ordered]


def fetch_take_facts(run: DramaApiRunSession, take_job_id: str, *, spine_id: str | None) -> dict[str, Any] | None:
    """Read a take's facts (never its prompt). A missing answer is logged, not fatal: the take is paid for.

    Parameters
    ----------
    run
        Active harness session.
    take_job_id
        The child job that filmed the take.
    spine_id
        When given, the facts list which approved line ids the take's instructions carry.

    Returns
    -------
    dict[str, Any] | None
        ``take_facts`` (lane, length, references, shots, lines, cue plan) or ``None``.
    """

    query = f"?spine_id={quote(spine_id, safe='')}" if spine_id else ""
    status, body = run.get_optional(f"/v1/jobs/{take_job_id}/take-facts{query}")
    if 200 <= status < 300 and isinstance(body, dict) and isinstance(body.get("take_facts"), dict):
        return dict(body["take_facts"])
    run.emit("take_facts_unavailable", take_job_id=take_job_id, status=status)
    return None
