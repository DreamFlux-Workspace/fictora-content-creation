"""Pull the server's current version of each episode into the desk (2 Nov 2026).

When the server repairs or re-sounds episodes, the desk can be behind. Server-heavy rule
(founder, 15 Oct 2026): the server owns which file is current. The kit asks, compares the
link with the link saved beside each file, and downloads what is new. It keeps no state.

Two read-only routes, nothing recorded and nothing made on the server:

* ``GET /v1/spines/{spine_id}/standing``: which episodes are finished, the run that delivered
  each, and that cut's length.
* ``GET /v1/video-generations/{job_id}``: that run's ``output.scenes[]``, whose ``video_url`` is
  the delivered cut.

A file already on the desk for the same link is "up to date". Otherwise the cut is saved as the
next numbered ``<slug>_epNN_draft_vN_<date>.mp4``; nothing is overwritten or deleted.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import httpx

from creation.harness.http_util import api_error_text
from creation.post.episode_name import saved_video
from creation.post.server_finish import (
    ServerFinishError,
    _api,
    _body,
    _route_missing,
    download_file,
)

STANDING_ROUTE = "/v1/spines/{spine_id}/standing"
JOB_ROUTE = "/v1/video-generations/{job_id}"
READ_TIMEOUT_SECONDS = 60.0
PULL_NOT_DONE = 5


@dataclass(frozen=True)
class Pulled:
    """One episode's outcome."""

    episode: int
    line: str
    saved: Path | None = None
    current: bool = False
    failed: bool = False


def _seconds(value: float | None) -> str:
    return f"{round(value)} s" if value is not None else "length unknown"


def _local_seconds(path: Path) -> float | None:
    from creation.post.media import media_duration

    try:
        return media_duration(path)
    except Exception:  # a length is only for the printed line
        return None


def _get(
    client: httpx.Client, base: str, headers: Mapping[str, str], path: str
) -> tuple[httpx.Response | None, Any]:
    try:
        response = client.get(base.rstrip("/") + path, headers=dict(headers))
    except httpx.HTTPError:
        return None, None
    return response, _body(response)


def _scene_url(body: Any, episode_id: str) -> tuple[str | None, int | None]:
    output = body.get("output") if isinstance(body, dict) else None
    scenes = output.get("scenes") if isinstance(output, dict) else None
    for scene in scenes if isinstance(scenes, list) else []:
        if (
            isinstance(scene, dict)
            and scene.get("episode_id") == episode_id
            and scene.get("video_url")
        ):
            return str(scene["video_url"]), scene.get("duration_ms")
    return None, None


def pull_episodes(
    desk: Path,
    *,
    episode: int | None = None,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    stream: TextIO | None = None,
) -> int:
    """Print one plain line per episode and save any newer cut. Free; never overwrites.

    Returns
    -------
    int
        ``0`` when every asked episode is current or saved; :data:`PULL_NOT_DONE` otherwise.
    """

    from creation.post.desk import spine_id as desk_spine_id
    from creation.post.episode_name import newest_episode_file, next_episode_path

    out = stream or sys.stdout
    desk = desk.expanduser().resolve()

    def say(line: str) -> None:
        print(line, file=out, flush=True)

    try:
        spine = desk_spine_id(desk)
        base, headers = api if api is not None else _api(desk)
    except (ValueError, FileNotFoundError) as exc:
        say(f"NOT DONE: {exc}")
        return PULL_NOT_DONE
    except SystemExit as exc:  # no kit token
        say(f"NOT DONE: {exc.code}")
        return PULL_NOT_DONE

    with httpx.Client(timeout=READ_TIMEOUT_SECONDS, transport=transport) as client:
        response, body = _get(
            client, base, headers, STANDING_ROUTE.format(spine_id=spine)
        )
        if response is None:
            say(
                "NOT DONE: could not reach the server. Check the internet connection and run it again. Nothing changed."
            )
            return PULL_NOT_DONE
        if response.status_code == 404 and _route_missing(body):
            say(
                "NOT DONE: this server cannot say which episode files are current yet (it needs the server update). Tell the core team. Nothing changed."
            )
            return PULL_NOT_DONE
        if not response.is_success or not isinstance(body, dict):
            say(
                f"NOT DONE: the server did not say which episodes are finished (HTTP {response.status_code}: {api_error_text(body)[:200]}). Nothing changed."
            )
            return PULL_NOT_DONE
        rows = {
            int(row["episode_ordinal"]): row
            for row in body.get("episodes") or []
            if isinstance(row, dict) and isinstance(row.get("episode_ordinal"), int)
        }
        wanted = (
            [episode]
            if episode is not None
            else sorted(n for n, r in rows.items() if r.get("video") == "finished")
        )
        jobs: dict[str, Any] = {}
        results: list[Pulled] = []
        for number in wanted:
            row = rows.get(number)
            if (
                row is None
                or row.get("video") != "finished"
                or not row.get("video_job_id")
                or not row.get("episode_id")
            ):
                results.append(
                    Pulled(
                        number,
                        f"ep {number}: the server has no finished episode {number} yet. Nothing changed.",
                        failed=episode is not None,
                    )
                )
                continue
            job_id = str(row["video_job_id"])
            if job_id not in jobs:
                answer, job_body = _get(
                    client, base, headers, JOB_ROUTE.format(job_id=job_id)
                )
                jobs[job_id] = (
                    job_body if answer is not None and answer.is_success else None
                )
            url, scene_ms = _scene_url(jobs[job_id], str(row["episode_id"]))
            if url is None:
                results.append(
                    Pulled(
                        number,
                        f"ep {number}: the server's delivered file could not be read. Nothing changed.",
                        failed=True,
                    )
                )
                continue
            folder = desk / f"ep{number:02d}" / "takes"
            have = saved_video(folder, number, url)
            if have is not None:
                results.append(
                    Pulled(number, f"ep {number}: already up to date", current=True)
                )
                continue
            before = newest_episode_file(folder, number) if folder.is_dir() else None
            was = _local_seconds(before) if before else None
            try:
                saved = download_file(
                    url,
                    next_episode_path(folder, desk, number, "draft"),
                    transport=transport,
                    kind="video",
                    meta={
                        "episode": number,
                        "video_url": url,
                        "generation_job_id": job_id,
                        "pulled": True,
                    },
                )
            except ServerFinishError as exc:
                results.append(
                    Pulled(number, f"ep {number}: not saved. {exc}", failed=True)
                )
                continue
            now = (scene_ms or row.get("duration_ms") or 0) / 1000 or None
            older = (
                f" Your older version is still here if you need it: {before.name} "
                f"({_seconds(was)})."
                if before
                else " There was no earlier file for this episode on the desk."
            )
            results.append(
                Pulled(
                    number,
                    f"ep {number}: new version saved as {saved.name} "
                    f"({_seconds(now)}) in {saved.parent}.{older}",
                    saved=saved,
                )
            )

    for item in results:
        say(item.line)
    saved_n = sum(1 for r in results if r.saved)
    current_n = sum(1 for r in results if r.current)
    failed_n = sum(1 for r in results if r.failed)
    say(
        f"{saved_n} new version(s) saved, {current_n} already up to date"
        + (f", {failed_n} not done" if failed_n else "")
        + ". Nothing was overwritten or deleted."
    )
    return PULL_NOT_DONE if failed_n else 0
