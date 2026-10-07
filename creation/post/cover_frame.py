"""The cover is the video's first frame (founder decision, 7 Oct 2026; desks from 6 Oct 2026).

Discord, WhatsApp, Telegram and Slack show an uploaded video's first frame as
its preview, and TikTok and Instagram start from it as the default cover; none
of them reads a cover attached inside the MP4 (``attached_pic``). So the reel
and the joined episode carry the reel's cover image (series title and "PART N",
:mod:`creation.post.reel_cover`) as frame 0.

Exactly one frame (about 0.04 s at the house 24 fps): too short to see while
the reel plays, so the hook and the length are unchanged. The sound is copied
as it is, so nothing moves; only the picture is encoded again (high quality),
and a cover attached inside the file is kept. The cover image is still written
beside the reel for Instagram's "Edit cover".

A desk created before 6 Oct 2026 keeps its first frame (rules epoch).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from creation.post.media import (
    MediaToolError,
    keep_cover_args,
    probe_video,
    run_ffmpeg,
    video_streams,
)

#: The picture's quality when frame 0 is replaced (libx264 CRF; 17 is visually lossless).
COVER_FRAME_CRF = "17"

_COVER = re.compile(r"^reel-ep(\d+)(-draft)?-v(\d+)-cover-v(\d+)\.jpg$")


def episode_cover(desk: Path, episode: int) -> Path | None:
    """The episode's newest reel cover image, a finished reel's before a draft's; ``None`` when none.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.

    Returns
    -------
    Path | None
        ``reels/epNN/reel-epNN-vN-cover-vM.jpg`` (or flat in ``reels/`` on an older desk).
    """

    found: list[tuple[int, int, int, Path]] = []
    for where in (desk / "reels" / f"ep{episode:02d}", desk / "reels"):
        if not where.is_dir():
            continue
        for path in where.iterdir():
            match = _COVER.match(path.name)
            if match and int(match.group(1)) == episode:
                finished = 0 if match.group(2) else 1
                found.append((finished, int(match.group(3)), int(match.group(4)), path))
    return max(found)[3] if found else None


def put_cover_on_first_frame(video: Path, cover: Path, out: Path) -> Path:
    """Write ``out``: ``video`` with ``cover`` as its first frame, same length, same sound.

    Parameters
    ----------
    video
        The finished MP4.
    cover
        The cover image (scaled and cropped to the picture's size).
    out
        The output MP4 (must not exist yet).

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    MediaToolError
        When ffmpeg fails.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; a cover frame never overwrites")
    info = probe_video(video)
    picture, attached = video_streams(video)
    w, h = info.width, info.height
    graph = (
        f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p[c];"
        f"[0:v:{picture}][c]overlay=0:0:enable='eq(n\\,0)':shortest=1:format=auto[v]"
    )
    run_ffmpeg(
        ["-i", str(video), "-loop", "1", "-i", str(cover), "-filter_complex", graph,
         "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-crf", COVER_FRAME_CRF, "-preset", "medium", "-pix_fmt", "yuv420p",
         *keep_cover_args(attached), "-c:a", "copy", "-movflags", "+faststart", str(out)]
    )  # fmt: skip
    return out


def cover_first_frame_in_place(video: Path, cover: Path) -> str | None:
    """Put ``cover`` on frame 0 of a file this command has just written (nobody has it open yet).

    Never stops the reel or the join: when ffmpeg fails, ``video`` is left exactly
    as it was written and the returned warning says so.

    Parameters
    ----------
    video
        The new reel or joined episode.
    cover
        The cover image.

    Returns
    -------
    str | None
        ``None`` when the cover is the first frame; else the ⚠ line to print.
    """

    try:
        with tempfile.TemporaryDirectory(prefix="fictora-cover-frame-") as tmp:
            done = put_cover_on_first_frame(video, cover, Path(tmp) / video.name)
            shutil.move(str(done), str(video))
    except (MediaToolError, OSError) as exc:
        return (
            f"⚠ cover frame skipped: {video.name} keeps its own first frame ({type(exc).__name__}: "
            f"{str(exc)[-200:]})"
        )
    return None
