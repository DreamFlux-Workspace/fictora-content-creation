"""The episode cover: made by the server, saved next to the finished video (free).

The Drama API makes every episode's cover itself (fictora-drama #998): a
1080x1920 JPEG of a clean frame with the show title, "EPISODE n" and a two-line
hook. This kit draws nothing. After ``finish`` it asks
``GET /v1/spines/{spine_id}/episodes/{n}/cover`` for the cover of the take's
stored clip (``?video_url=``; the kit uploads nothing, so that clip is the
take's own file in our storage) and saves the JPEG as ``cover-epNN-vN.jpg`` in
the episode's ``takes`` folder, beside the finished video. The server keeps one
cover per story, episode and clip, so asking again is free and the same file on
the desk is reused.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx

from creation.harness.http_util import api_error_text
from creation.media_fetch import download_to_versioned
from creation.post.desk import open_api, spine_id, take_stored_url

#: The route this module reads (fictora-drama ``spine_episode_cover``).
COVER_ROUTE = "/v1/spines/{spine_id}/episodes/{ordinal}/cover"

Downloader = Callable[[str, Path, str], Path]


@dataclass(frozen=True)
class SavedCover:
    """What the cover step did.

    ``path`` is the JPEG on the desk (``None`` when no cover was saved) and
    ``note`` says why in plain words.
    """

    path: Path | None
    note: str
    cover_url: str | None = None
    reused: bool = False


def cover_path(spine: str, episode: int, video_url: str | None) -> str:
    """The Drama API path for one episode's cover, with the take's clip when there is one."""

    path = COVER_ROUTE.format(spine_id=spine, ordinal=episode)
    if video_url:
        path += f"?video_url={quote(video_url, safe='')}"
    return path


def _version(path: Path) -> int:
    tail = path.stem.rsplit("-v", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _meta_name(cover: Path) -> str:
    return f"{cover.stem}.json"


def saved_episode_cover(takes_dir: Path, episode: int, cover_url: str) -> Path | None:
    """The cover already on the desk for this exact server cover, if any (newest first)."""

    covers = sorted(
        takes_dir.glob(f"cover-ep{episode:02d}-v*.jpg"),
        key=_version,
        reverse=True,
    )
    for cover in covers:
        meta = takes_dir / _meta_name(cover)
        if not meta.is_file():
            continue
        try:
            body = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(body, dict) and body.get("cover_url") == cover_url:
            return cover
    return None


def _download(url: str, directory: Path, stem: str) -> Path:
    with httpx.Client(timeout=120.0) as client:
        return download_to_versioned(
            client, url, directory, stem, default_suffix=".jpg"
        )


def fetch_episode_cover(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    takes_dir: Path,
    downloader: Downloader | None = None,
) -> SavedCover:
    """Ask the server for the episode's cover and save it beside the finished video.

    Never raises for a server answer: a cover that cannot be had leaves the
    finished video as it is, with a plain note.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        The finished take.
    takes_dir
        ``epNN/takes`` (where the finished video is).
    downloader
        ``(url, directory, stem) -> path``; injected for tests.

    Returns
    -------
    SavedCover
        The saved JPEG (or the one already there for the same cover) and a note.
    """

    video_url = take_stored_url(desk, episode, take_id)
    run = open_api(desk, episode)
    try:
        status, body = run.get_optional(cover_path(spine_id(desk), episode, video_url))
    finally:
        run.client.close()
    run.save(f"ep{episode:02d}_cover_response.json", {"status": status, "body": body})
    if status == 404 and _route_missing(body):
        return SavedCover(
            None, "this server has no episode cover route yet; no cover saved"
        )
    if not 200 <= status < 300 or not isinstance(body, dict):
        return SavedCover(
            None, f"no cover this time (HTTP {status}: {api_error_text(body)[:200]})"
        )
    cover_url = body.get("cover_url")
    if not cover_url:
        return SavedCover(None, "the server answered without a cover; no cover saved")
    cover_url = str(cover_url)
    existing = saved_episode_cover(takes_dir, episode, cover_url)
    if existing is not None:
        return SavedCover(
            existing,
            f"cover `{existing.name}` is already saved (free)",
            cover_url,
            True,
        )
    saved = (downloader or _download)(cover_url, takes_dir, f"cover-ep{episode:02d}")
    meta: dict[str, Any] = {
        "cover_url": cover_url,
        "episode": episode,
        "take_id": take_id,
        "video_url": video_url,
        "width": body.get("width"),
        "height": body.get("height"),
        "cached": body.get("cached"),
    }
    (takes_dir / _meta_name(saved)).write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return SavedCover(saved, f"cover saved as `{saved.name}` (free)", cover_url)


def _route_missing(body: Any) -> bool:
    """A 404 from an older deploy (FastAPI's bare ``Not Found``), not an episode the server knows is missing."""

    return isinstance(body, dict) and body.get("detail") == "Not Found"
