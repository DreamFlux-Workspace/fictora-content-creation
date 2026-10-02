"""Episode thumbnail: server draw + attached cover on the finished take.

A cover drawn on the server is a paid still, so ``finish`` never draws one
without the operator's opt-in (``--thumbnail``, after the human's yes to
:data:`THUMBNAIL_USD`). A cover already on the desk for the same clip
(``take-epNN-tK-thumb-vN.jpg``) is re-embedded locally, free, on every finish.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TextIO

import httpx

from creation.harness.http_util import api_error_text
from creation.harness.thumbnail_api import (
    episode_thumbnail_route_missing,
    post_episode_thumbnail,
    server_draws_episode_thumbnail,
)
from creation.harness_rules import THUMBNAIL_AUDIO_ERROR, thumbnail_answered_audio_error
from creation.media_fetch import download_to_versioned
from creation.ops.folder import next_versioned_path
from creation.post.desk import open_api, spine_id, take_stored_url
from creation.post.media import run_ffmpeg
from creation.prices import STILL_USD

#: What one episode cover drawn on the server costs (one still; free when the
#: server already drew the same clip).
THUMBNAIL_USD = float(STILL_USD)


def embed_attached_cover(*, video: Path, cover: Path, out: Path) -> None:
    """Write ``out`` as a copy of ``video`` with ``cover`` as the attached picture.

    Parameters
    ----------
    video
        Finished MP4.
    cover
        JPEG or PNG cover art.
    out
        Output MP4 (must not exist yet; caller uses versioned paths).

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    """

    ext = cover.suffix.lower()
    codec = "mjpeg" if ext in {".jpg", ".jpeg"} else "png"
    run_ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(cover),
            "-map",
            "0",
            "-map",
            "1",
            "-c",
            "copy",
            "-c:v:1",
            codec,
            "-disposition:v:1",
            "attached_pic",
            str(out),
        ]
    )


def saved_cover(takes_dir: Path, base_stem: str, video_url: str | None) -> Path | None:
    """The newest cover already on the desk for this take's clip, if any.

    Parameters
    ----------
    takes_dir
        ``epNN/takes``.
    base_stem
        Filename stem without version (``take-ep01-t1``).
    video_url
        The take's stored clip URL. A cover whose meta file names a different
        clip (the take was filmed again) is not reused.

    Returns
    -------
    Path | None
        ``take-epNN-tK-thumb-vN.jpg`` (or ``.png``), newest first; ``None`` when
        there is none for this clip.
    """

    covers = sorted(
        (
            path
            for path in takes_dir.glob(f"{base_stem}-thumb-v*.*")
            if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ),
        key=_version,
        reverse=True,
    )
    metas = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in takes_dir.glob(f"{base_stem}-thumb-meta-v*.json")
    ]
    drawn_from = {
        str(meta.get("cover_path")): meta.get("video_url")
        for meta in metas
        if isinstance(meta, dict)
    }
    for cover in covers:
        clip = drawn_from.get(cover.name)
        if clip and video_url and clip != video_url:
            continue
        return cover
    return None


def _version(path: Path) -> int:
    tail = path.stem.rsplit("-v", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def attach_episode_thumbnail_to_finish(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    marked_video: Path,
    takes_dir: Path,
    base_stem: str,
    draw: bool = False,
    stream: TextIO | None = None,
) -> tuple[Path, dict[str, Any] | None]:
    """Embed the episode cover on the marked deliverable; draw it on the server only when asked.

    A cover already on the desk for this clip is re-embedded locally (free, no
    call). Without one, nothing is drawn unless ``draw`` is true; with ``draw``
    the price is printed before the server is asked.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take id (``t1``).
    marked_video
        The watermarked MP4 to attach the cover to.
    takes_dir
        ``epNN/takes``.
    base_stem
        Filename stem without version (``take-ep01-t1``).
    draw
        The operator opted in (``finish --thumbnail``) to a paid server draw
        when no cover is on the desk yet.
    stream
        Progress output.

    Returns
    -------
    tuple[Path, dict[str, Any] | None]
        Final video path (with cover when one went on) and the answer: the
        server's, or ``{"reused": <cover name>, "cost_usd": 0}`` for a saved
        cover, or ``{"needs_opt_in": True, ...}`` when nothing was drawn
        because ``draw`` was false. ``None`` when there is no stored clip URL or
        the deploy has no thumbnail route.
    """

    video_url = take_stored_url(desk, episode, take_id)
    saved = saved_cover(takes_dir, base_stem, video_url)
    if saved is not None:
        out = next_versioned_path(takes_dir, f"{base_stem}-sokii-cover", ".mp4")
        embed_attached_cover(video=marked_video, cover=saved, out=out)
        return out, {"reused": saved.name, "cost_usd": 0.0, "cached": True}
    if not video_url:
        return marked_video, None
    if not draw:
        return marked_video, {"needs_opt_in": True, "cost_usd": 0.0}
    if stream is not None:
        print(
            f"[thumbnail] Drawing the episode cover on the server: ${THUMBNAIL_USD:.2f} "
            "(free if the server already drew this clip)",
            file=stream,
            flush=True,
        )

    run = open_api(desk, episode)
    try:
        if server_draws_episode_thumbnail(run) is False:
            msg = "this Drama API has no episode thumbnail route yet (404 on POST …/thumbnail)"
            if stream is not None:
                print(f"[thumbnail] skipped: {msg}", file=stream, flush=True)
            return marked_video, None

        digest = hashlib.sha256(video_url.encode()).hexdigest()[:12]
        answer = post_episode_thumbnail(
            run,
            spine_id=spine_id(desk),
            episode=episode,
            video_url=video_url,
            idempotency_key=f"{run.prefix}-ep{episode:02d}-{take_id}-thumb-{digest}",
        )
    except SystemExit as exc:
        text = str(exc.code)
        if episode_thumbnail_route_missing(text):
            if stream is not None:
                print(
                    "[thumbnail] skipped: server has no episode thumbnail route yet",
                    file=stream,
                    flush=True,
                )
            return marked_video, None
        if thumbnail_answered_audio_error(text):
            if stream is not None:
                print(f"[thumbnail] skipped: {THUMBNAIL_AUDIO_ERROR}", file=stream, flush=True)
            return marked_video, {"skipped": THUMBNAIL_AUDIO_ERROR, "cost_usd": 0.0}
        raise
    finally:
        run.client.close()

    image_url = answer.get("image_url")
    if not image_url:
        raise SystemExit(
            f"episode thumbnail answer has no image_url: {api_error_text(answer)}"
        )

    with httpx.Client(timeout=120.0) as client:
        cover = download_to_versioned(
            client,
            str(image_url),
            takes_dir,
            f"{base_stem}-thumb",
            default_suffix=".jpg",
        )
    out = next_versioned_path(takes_dir, f"{base_stem}-sokii-cover", ".mp4")
    embed_attached_cover(video=marked_video, cover=cover, out=out)
    meta = next_versioned_path(takes_dir, f"{base_stem}-thumb-meta", ".json")
    meta.write_text(
        json.dumps(
            {**answer, "cover_path": str(cover.name), "video_url": video_url},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out, answer
