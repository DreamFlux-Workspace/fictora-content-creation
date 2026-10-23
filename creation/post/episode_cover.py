"""The episode cover: made by the server from the finished episode, saved next to it (free).

The Drama API makes every episode's cover itself (fictora-drama #998): a
1080x1920 JPEG of a clean frame with the show title, "EPISODE n" and a two-line
hook. This kit draws nothing. Once per episode, when the file the kit delivers
is made (``join`` of one episode, or ``finish`` of an episode's only take), it
uploads that finished file (``POST /v1/uploads`` and the signed ``PUT``), asks
``GET /v1/spines/{spine_id}/episodes/{n}/cover?video_url=<the upload>`` and
saves the JPEG as ``cover-epNN-vN.jpg`` beside the video. The server keeps one
cover per story, episode and video, so asking again is free; the same file
already on the desk is reused and a new cover gets the next version.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from creation.harness.http_util import api_error_text
from creation.media_fetch import MEDIA_HEADERS, download_to_versioned
from creation.post.desk import open_api, spine_id

#: The route this module reads (fictora-drama ``spine_episode_cover``).
COVER_ROUTE = "/v1/spines/{spine_id}/episodes/{ordinal}/cover"
#: The signed upload slot route (fictora-drama ``create_operator_upload``).
UPLOAD_ROUTE = "/v1/uploads"
#: The server's cap for one ``video/mp4`` upload.
VIDEO_UPLOAD_MAX_BYTES = 300 * 1024 * 1024
#: Seconds one upload or download may take.
TRANSFER_TIMEOUT_SECONDS = 300.0

Downloader = Callable[[str, Path, str], Path]
Putter = Callable[[str, bytes, Mapping[str, str]], int]


class CoverStepError(RuntimeError):
    """The upload or the cover could not be had; the message is one plain line."""


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


def cover_path(spine: str, episode: int, video_url: str) -> str:
    """The Drama API path for the cover of one finished episode file in our storage."""

    return (
        COVER_ROUTE.format(spine_id=spine, ordinal=episode)
        + f"?video_url={quote(video_url, safe='')}"
    )


def file_sha256(path: Path) -> str:
    """The file's SHA-256 (hex)."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(path: Path) -> int:
    tail = path.stem.rsplit("-v", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _meta_name(cover: Path) -> str:
    return f"{cover.stem}.json"


def _metas(folder: Path, episode: int) -> list[tuple[Path, dict[str, Any]]]:
    """Saved covers with their notes, newest first."""

    found: list[tuple[Path, dict[str, Any]]] = []
    for cover in sorted(
        folder.glob(f"cover-ep{episode:02d}-v*.jpg"), key=_version, reverse=True
    ):
        meta = folder / _meta_name(cover)
        try:
            body = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(body, dict):
            found.append((cover, body))
    return found


def saved_episode_cover(folder: Path, episode: int, cover_url: str) -> Path | None:
    """The cover already on the desk for this exact server cover, if any (newest first)."""

    return next(
        (
            cover
            for cover, body in _metas(folder, episode)
            if body.get("cover_url") == cover_url
        ),
        None,
    )


def _uploaded_before(folder: Path, episode: int, sha: str) -> str | None:
    """The stored URL of this exact file from an earlier cover, so it is not uploaded twice."""

    return next(
        (
            str(body["video_url"])
            for _cover, body in _metas(folder, episode)
            if body.get("video_sha256") == sha and body.get("video_url")
        ),
        None,
    )


def _put(url: str, content: bytes, headers: Mapping[str, str]) -> int:
    with httpx.Client(
        timeout=TRANSFER_TIMEOUT_SECONDS, follow_redirects=True, headers=MEDIA_HEADERS
    ) as client:
        return client.put(url, content=content, headers=dict(headers)).status_code


def _download(url: str, directory: Path, stem: str) -> Path:
    with httpx.Client(timeout=TRANSFER_TIMEOUT_SECONDS) as client:
        return download_to_versioned(
            client, url, directory, stem, default_suffix=".jpg"
        )


def upload_video(run: Any, video: Path, *, putter: Putter | None = None) -> str:
    """Upload one finished MP4 into our storage and return its stored URL.

    Parameters
    ----------
    run
        An open API session (``post_optional``).
    video
        The finished episode file.
    putter
        ``(url, bytes, headers) -> HTTP status`` for the signed ``PUT``; injected for tests.

    Returns
    -------
    str
        The stored file URL the cover route reads.

    Raises
    ------
    CoverStepError
        When the server refuses the slot or the storage refuses the bytes.
    """

    size = video.stat().st_size
    if size > VIDEO_UPLOAD_MAX_BYTES:
        raise CoverStepError(
            f"`{video.name}` is {size // (1024 * 1024)} MB, over the server's 300 MB upload cap"
        )
    status, slot = run.post_optional(
        UPLOAD_ROUTE, {"content_type": "video/mp4", "size_bytes": size}
    )
    if not 200 <= status < 300 or not isinstance(slot, Mapping):
        raise CoverStepError(
            f"the server would not take the upload (HTTP {status}: {api_error_text(slot)[:200]})"
        )
    stored = str(slot.get("file_url") or slot.get("audio_url") or "")
    if not stored or not slot.get("upload_url"):
        raise CoverStepError("the server's upload answer named no file URL")
    headers = {str(k): str(v) for k, v in (slot.get("headers") or {}).items()}
    answered = (putter or _put)(str(slot["upload_url"]), video.read_bytes(), headers)
    if not 200 <= answered < 300:
        raise CoverStepError(f"the storage refused the upload (HTTP {answered})")
    return stored


def fetch_episode_cover(
    desk: Path,
    *,
    episode: int,
    video: Path,
    downloader: Downloader | None = None,
    putter: Putter | None = None,
) -> SavedCover:
    """Upload the finished episode, ask the server for its cover and save it beside the video.

    Never raises: a cover that cannot be had leaves the finished video as it
    is, with one plain line saying why.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    video
        The finished episode file the kit delivers.
    downloader
        ``(url, directory, stem) -> path`` for the cover JPEG; injected for tests.
    putter
        The signed ``PUT`` (see :func:`upload_video`); injected for tests.

    Returns
    -------
    SavedCover
        The saved JPEG (or the one already there for the same cover) and a note.
    """

    folder = video.parent
    try:
        sha = file_sha256(video)
        run = open_api(desk, episode)
    except (OSError, SystemExit, ValueError) as exc:
        detail = str(exc.code) if isinstance(exc, SystemExit) else str(exc)
        return SavedCover(None, f"no cover this time: {detail[:200]}")
    try:
        video_url = _uploaded_before(folder, episode, sha) or upload_video(
            run, video, putter=putter
        )
        status, body = run.get_optional(cover_path(spine_id(desk), episode, video_url))
        run.save(
            f"ep{episode:02d}_cover_response.json", {"status": status, "body": body}
        )
        if status == 404 and _route_missing(body):
            raise CoverStepError("this server has no episode cover route yet")
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise CoverStepError(
                f"the server could not make the cover (HTTP {status}: {api_error_text(body)[:200]})"
            )
        cover_url = str(body.get("cover_url") or "")
        if not cover_url:
            raise CoverStepError("the server answered without a cover")
        existing = saved_episode_cover(folder, episode, cover_url)
        if existing is not None:
            return SavedCover(
                existing,
                f"cover `{existing.name}` is already saved (free)",
                cover_url,
                True,
            )
        saved = (downloader or _download)(cover_url, folder, f"cover-ep{episode:02d}")
    except (CoverStepError, httpx.HTTPError, OSError, SystemExit, ValueError) as exc:
        if isinstance(exc, SystemExit):
            detail = str(exc.code)
        elif isinstance(exc, httpx.HTTPError):
            detail = f"the server or storage did not answer ({type(exc).__name__})"
        else:
            detail = str(exc)
        return SavedCover(None, f"no cover this time: {detail[:240]}")
    finally:
        run.client.close()
    meta: dict[str, Any] = {
        "cover_url": cover_url,
        "episode": episode,
        "video": video.name,
        "video_sha256": sha,
        "video_url": video_url,
        "width": body.get("width"),
        "height": body.get("height"),
        "cached": body.get("cached"),
    }
    (folder / _meta_name(saved)).write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return SavedCover(saved, f"cover saved as `{saved.name}` (free)", cover_url)


def other_takes(desk: Path, episode: int, take_id: str) -> list[str]:
    """The episode's other takes: none means this take's finished file is the whole episode.

    Read from the desk's series (the takes the episode was planned with) and
    any other take with a finish record.
    """

    from creation.ops.state import episode_by_ordinal, load_series

    try:
        planned = [
            take.take_id
            for take in episode_by_ordinal(load_series(desk), episode).takes
        ]
    except (FileNotFoundError, ValueError, KeyError):
        planned = []
    takes = desk / f"ep{episode:02d}" / "takes"
    finished = {
        path.name.split("-")[2]
        for path in takes.glob(f"take-ep{episode:02d}-t*-finish-v*.json")
    }
    return sorted((set(planned) | finished) - {take_id})


def _route_missing(body: Any) -> bool:
    """A 404 from an older deploy (FastAPI's bare ``Not Found``), not an episode the server knows is missing."""

    return isinstance(body, dict) and body.get("detail") == "Not Found"
