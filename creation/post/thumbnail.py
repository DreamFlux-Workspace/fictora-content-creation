"""Episode thumbnail: server draw + attached cover on the finished take."""

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
from creation.media_fetch import download_to_versioned
from creation.ops.folder import next_versioned_path
from creation.post.desk import open_api, spine_id, take_stored_url
from creation.post.media import run_ffmpeg


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


def attach_episode_thumbnail_to_finish(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    marked_video: Path,
    takes_dir: Path,
    base_stem: str,
    stream: TextIO | None = None,
) -> tuple[Path, dict[str, Any] | None]:
    """Draw the episode thumbnail on the server and embed it on the marked deliverable.

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
    stream
        Progress output.

    Returns
    -------
    tuple[Path, dict[str, Any] | None]
        Final video path (with cover when the draw ran) and the API answer, if any.
    """

    video_url = take_stored_url(desk, episode, take_id)
    if not video_url:
        return marked_video, None

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
        json.dumps({**answer, "cover_path": str(cover.name)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return out, answer
