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
import sys
import time

import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from creation.harness.http_util import (
    STALE_JOB_SECONDS,
    _parse_utc,
    connection_dropped,
    describe_job_error,
    stale_job_warning,
)
from creation.harness.session import DramaApiRunSession

_SET_SUFFIX = re.compile(r"_set(\d+)$")
_FAILED = frozenset({"failed", "cancelled"})


class VideoJobFailed(SystemExit):
    """The coordinator or a take job ended ``failed`` / ``cancelled``: that job is over.

    A :class:`SystemExit` like every other stop of the poll, so callers that
    stop loud keep doing so. Callers that keep a pending job to pick up after an
    interruption tell it apart: polling a failed job again only repeats the
    failure, while a timeout or a broken connection leaves a job that may still
    finish (and was paid for).
    """


STEP_RAW_CLIPS = "17_raw_scene_clips.json"
"""The clip record ``step`` writes in ``epNN/api`` (also what ``adopt-desk`` builds for an old desk)."""

_UNIT_RAW_CLIPS_SUFFIX = "-raw-scene-clips.json"


def raw_clips_name(unit: str | None = None) -> str:
    """Name of the clip record a filming writes in ``epNN/api``.

    ``step`` keeps :data:`STEP_RAW_CLIPS`; a ``film`` unit (``film-ep02-t2-s2``)
    keeps its own record so the step's is never overwritten. Every reader finds
    both through :func:`raw_clips_records`.

    Parameters
    ----------
    unit
        The ``film`` unit, or ``None`` for ``step``.

    Returns
    -------
    str
        The file name.
    """

    return f"{unit}{_UNIT_RAW_CLIPS_SUFFIX}" if unit else STEP_RAW_CLIPS


def raw_clips_records(api_dir: Path) -> list[Path]:
    """Every clip record in an episode's ``api/`` folder, newest first.

    The step's :data:`STEP_RAW_CLIPS` and each ``film`` unit's record
    (:func:`raw_clips_name`), ordered by when they were written, so a take
    filmed again is read from the film that filmed it last.

    Parameters
    ----------
    api_dir
        ``epNN/api``.

    Returns
    -------
    list[Path]
        Existing records, newest first (empty when the episode was never filmed).
    """

    if not api_dir.is_dir():
        return []
    found = [
        path
        for path in api_dir.iterdir()
        if path.is_file()
        and (path.name == STEP_RAW_CLIPS or path.name.endswith(_UNIT_RAW_CLIPS_SUFFIX))
    ]
    return sorted(
        found, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True
    )


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


def _print_warning(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr, flush=True)


def video_generation_failure(
    run: DramaApiRunSession, coordinator_job_id: str
) -> dict[str, Any] | None:
    """The ``/v1/video-generations/{id}`` record when that route says the filming ended failed or cancelled.

    ``/v1/jobs/{id}`` can still read ``running`` (progress 0) after the
    video-generation route has reported ``failed`` (seen twice, up to an hour
    of "running 0%"). The film poll reads both and stops on whichever ends
    first. A route that does not answer (an older server, a blip) is not a
    failure: the job poll carries on.

    Parameters
    ----------
    run
        Active harness session.
    coordinator_job_id
        The video generation's job id.

    Returns
    -------
    dict[str, Any] | None
        The failed or cancelled record, else ``None``.
    """

    status, body = run.get_optional(f"/v1/video-generations/{coordinator_job_id}")
    if not (200 <= status < 300) or not isinstance(body, dict):
        return None
    record = _job_record(body)
    return record if str(record.get("status") or "") in _FAILED else None


def stuck_film_warning(
    *, minutes: int, last_update: datetime, job_id: str, desk: str | None
) -> str:
    """The stuck warning for a film whose job has not moved: what to check, never a paid retry.

    Parameters
    ----------
    minutes
        Whole minutes without a change.
    last_update
        When it last changed (UTC).
    job_id
        The video job.
    desk
        The desk, for the commands (``D`` when unknown).

    Returns
    -------
    str
        One warning.
    """

    return (
        f"video job {job_id}: "
        + stale_job_warning(
            minutes=minutes, last_update=last_update, job_id=job_id, desk=desk
        )
        + " Do not film again or pass --confirm-spend again while it runs (that starts another paid job); "
        "tell the human and send the job id to engineering. The poll keeps watching and stops at once if "
        "the server reports it failed."
    )


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
    save_as: str = STEP_RAW_CLIPS,
    expected_clips: int | None = None,
    stale_after_seconds: float = STALE_JOB_SECONDS,
    warn: Callable[[str], None] | None = _print_warning,
) -> dict[str, Any]:
    """Poll until the coordinator is complete and every take job on it has a clip URL.

    The coordinator lists its take jobs in ``depends_on`` as it starts them, so
    a 30 s take at 50 % lists only its first clip: every listed child done is
    not the take done (L-20260925-1). The clips are returned only once the
    coordinator itself is ``completed`` and, when ``expected_clips`` is given,
    it lists at least that many take jobs (more is another episode's clip,
    which the caller reports).

    Stops loud, with the server's code and rule, when the coordinator or a take
    job fails: a take the server refuses (for example one that would drop an
    approved line) fails before any child is filmed. Each poll also reads
    ``/v1/video-generations/{id}`` (:func:`video_generation_failure`): the job
    route can still say ``running`` 0 % after that route reported ``failed``.

    When nothing moves (the job's status, ``updated_at`` and progress, the take
    jobs listed, the clips collected) for ``stale_after_seconds`` (10 minutes), ``warn`` gets
    :func:`stuck_film_warning` with the job id and what to do, and again every
    ``stale_after_seconds`` while nothing moves. Nothing is cancelled or retried.

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
        Artefact name for the clip list (:func:`raw_clips_name`: a ``film`` keeps its own, never the step's).
    expected_clips
        How many takes (storyboard sets) this film asked for; ``None`` when the caller cannot tell.
    stale_after_seconds
        How long without a change before the stuck warning (and between repeats).
    warn
        Where the stuck warning goes (stderr); ``None`` stays quiet.

    Returns
    -------
    dict[str, Any]
        ``{"coordinator_job_id", "coordinator_status": "completed", "clips": [{"job_id", "url",
        "relation_id", "set_index", "episode_id"}]}``.

    Raises
    ------
    VideoJobFailed
        When the coordinator, its video generation or a take job ended failed or cancelled.
    SystemExit
        At the deadline, when a poll is refused (the job may still finish), or
        when the completed coordinator lists fewer takes than were asked for (nothing is saved; the job is not forgotten).
    """

    deadline = time.monotonic() + deadline_seconds
    child_ids: list[str] = []
    clips: list[dict[str, Any]] = []
    seen: tuple[Any, ...] | None = None
    changed_at = time.monotonic()
    changed_utc = datetime.now(timezone.utc)
    next_warning = changed_at + stale_after_seconds
    desk_hint = getattr(run, "desk_hint", None)
    desk = desk_hint() if callable(desk_hint) else None

    while time.monotonic() < deadline:
        try:
            parent = _job_record(run.get(f"/v1/jobs/{coordinator_job_id}"))
        except httpx.HTTPError as exc:
            raise SystemExit(connection_dropped("the film job", exc)) from exc
        if str(parent.get("status") or "") in _FAILED:
            run.save("17_video_terminal.json", parent)
            raise VideoJobFailed(
                f"video job {coordinator_job_id} {describe_job_error(parent)}"
            )
        generation = video_generation_failure(run, coordinator_job_id)
        if generation is not None:
            run.save("17_video_terminal.json", generation)
            raise VideoJobFailed(
                f"video job {coordinator_job_id} {describe_job_error(generation)} "
                f"(reported by /v1/video-generations/{coordinator_job_id}; /v1/jobs still said "
                f"{parent.get('status') or 'nothing'} {parent.get('progress') or 0}%)"
            )
        depends = parent.get("depends_on")
        if isinstance(depends, list) and depends:
            child_ids = [str(item) for item in depends if item]
        if child_ids:
            pending = False
            clips = []
            for child_id in child_ids:
                try:
                    child = _job_record(run.get(f"/v1/jobs/{child_id}"))
                except httpx.HTTPError as exc:
                    raise SystemExit(connection_dropped("the film job", exc)) from exc
                status = str(child.get("status") or "")
                if status in {"queued", "running", ""}:
                    pending = True
                    continue
                if status in _FAILED:
                    raise VideoJobFailed(
                        f"take job {child_id} {describe_job_error(child)}"
                    )
                url = clip_url_from_job_payload(child)
                if url is None:
                    pending = True
                    continue
                relation = (
                    child.get("relation")
                    if isinstance(child.get("relation"), dict)
                    else {}
                )
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
            parent_done = str(parent.get("status") or "") == "completed"
            if (
                parent_done
                and expected_clips is not None
                and len(child_ids) < expected_clips
            ):
                raise SystemExit(
                    f"video job {coordinator_job_id} completed with {len(child_ids)} take job(s) but this film "
                    f"asked for {expected_clips}: the take is short; nothing was collected or marked done. Check the job on the "
                    "server before filming again (the clips it has are paid for)."
                )
            if parent_done and clips and not pending and len(clips) == len(child_ids):
                payload = {
                    "coordinator_job_id": coordinator_job_id,
                    "coordinator_status": "completed",
                    "clips": clips,
                }
                run.save(save_as, payload)
                return payload
        elif str(parent.get("status") or "") == "completed":
            raise SystemExit(
                f"video job {coordinator_job_id} completed with no take jobs in depends_on"
            )
        run.emit(
            "poll_raw_scenes",
            status="running",
            progress=parent.get("progress"),
            job_id=coordinator_job_id,
            children=len(child_ids),
            clips=len(clips),
            expected=expected_clips,
        )
        now = time.monotonic()
        marker = (
            parent.get("updated_at"),
            parent.get("progress"),
            parent.get("status"),
            len(child_ids),
            len(clips),
        )
        if marker != seen:
            seen = marker
            changed_at = now
            changed_utc = _parse_utc(parent.get("updated_at")) or datetime.now(
                timezone.utc
            )
            next_warning = now + stale_after_seconds
        elif warn is not None and now >= next_warning:
            warn(
                stuck_film_warning(
                    minutes=int((now - changed_at) // 60),
                    last_update=changed_utc,
                    job_id=coordinator_job_id,
                    desk=desk,
                )
            )
            next_warning += stale_after_seconds
        time.sleep(interval_seconds)

    raise SystemExit(
        f"timed out waiting for raw scene clips on {coordinator_job_id} "
        f"({len(clips)} clip(s) of {len(child_ids)} listed"
        + (f", {expected_clips} asked for" if expected_clips is not None else "")
        + "; the video job is not complete, so the take is NOT done)"
    )


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

    mine = [
        clip
        for clip in raw.get("clips") or []
        if clip.get("episode_id") in ("", episode_id)
    ]
    ordered = sorted(
        enumerate(mine),
        key=lambda pair: (pair[1].get("set_index") or pair[0] + 1, pair[0]),
    )
    return [clip for _, clip in ordered]


def fetch_take_facts(
    run: DramaApiRunSession, take_job_id: str, *, spine_id: str | None
) -> dict[str, Any] | None:
    """Read a take's facts (never its prompt). A missing answer is logged, not fatal: the take is paid for.

    Parameters
    ----------
    run
        Active harness session.
    take_job_id
        The child job that filmed the take.
    spine_id
        When given, the facts list which approved line ids the take's instructions carry, and
        their SFX plan carries the story's drop and level sound notes (fictora-drama #475).
        Every kit fetch passes it.

    Returns
    -------
    dict[str, Any] | None
        ``take_facts`` (lane, length, references, shots, lines, cue plan) or ``None``.
    """

    query = f"?spine_id={quote(spine_id, safe='')}" if spine_id else ""
    status, body = run.get_optional(f"/v1/jobs/{take_job_id}/take-facts{query}")
    if (
        200 <= status < 300
        and isinstance(body, dict)
        and isinstance(body.get("take_facts"), dict)
    ):
        return dict(body["take_facts"])
    run.emit("take_facts_unavailable", take_job_id=take_job_id, status=status)
    return None
