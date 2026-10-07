"""The cover is the video's first frame (founder decision, 7 Oct 2026; desks from 6 Oct 2026).

Discord, WhatsApp, Telegram and Slack show an uploaded video's first frame as
its preview, and TikTok and Instagram start from it as the default cover; none
of them reads a cover attached inside the MP4 (``attached_pic``). So the reel
and the joined episode carry the reel's cover image (series title and "PART N",
:mod:`creation.post.reel_cover`) as frame 0.

Exactly one frame (about 0.04 s at the house 24 fps): too short to see while
the reel plays, so the hook and the length are unchanged. The cover goes on in
the encode the command already makes (the mark step: :func:`cover_frame_graph`),
never as a second pass; the server reel engine does the same for its reels
(fictora-drama ``reel-rules-v5``). The cover image is still written beside the
reel for Instagram's "Edit cover". Every desk, older ones included (founder,
7 Oct 2026).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TextIO

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


def cover_frame_graph(
    *, cover_input: int, picture: str, width: int, height: int, out: str
) -> str:
    """The filter that lays the cover over frame 0 of ``picture``, for a command's own encode.

    Parameters
    ----------
    cover_input
        The cover's input index (given as ``-i COVER``: one picture, never ``-loop 1``; a
        looped picture that cannot be decoded makes ffmpeg read it forever).
    picture
        The picture's label in the graph (``[v]``).
    width, height
        The picture's size (the cover is filled to it).
    out
        The output label (``[vc]``).

    Returns
    -------
    str
        Two filter chains joined by ``;``, to append to the command's graph.
    """

    return (
        f"[{cover_input}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,format=yuv420p[cover];"
        f"{picture}[cover]overlay=0:0:enable='eq(n\\,0)':format=auto{out}"
    )


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
        f"[0:v:{picture}][c]overlay=0:0:enable='eq(n\\,0)':format=auto[v]"
    )
    run_ffmpeg(
        ["-i", str(video), "-i", str(cover), "-filter_complex", graph,
         "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-crf", COVER_FRAME_CRF, "-preset", "medium", "-pix_fmt", "yuv420p",
         *keep_cover_args(attached), "-c:a", "copy", "-movflags", "+faststart", str(out)]
    )  # fmt: skip
    return out


def cover_one_take_final(desk: Path, episode: int, result: Any, out: TextIO) -> None:
    """A one-take episode (15 s): the reel's cover on frame 0 of the episode's final file.

    A 15 s episode is never joined: ``finish``'s final file is what gets posted. Its cover
    is the reel's, the strongest moment the reel picked (founder, 7 Oct 2026, option B),
    so it goes on after the reel: one encode of a 15 s file, swapped in whole (a player
    that has it open keeps the old copy). Never stops anything: a failure is a ⚠ line and
    the file stays as it was.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    result
        The reel just made (:class:`creation.post.reel.ReelResult`), or ``None``.
    out
        Where the line goes.
    """

    if result is None or result.cover is None or result.draft or result.video is None:
        return
    from creation.ops.state import episode_by_ordinal, load_series
    from creation.post.finish_record import latest_finish_record

    desk = desk.expanduser().resolve()
    try:
        takes = episode_by_ordinal(load_series(desk), episode).takes
    except (FileNotFoundError, ValueError, KeyError) as exc:
        # Never stops the reel: with no readable series desk there is no take count to go by.
        print(
            f"cover frame: final file left as it was (the desk's takes could not be read: {exc})",
            file=out,
        )
        return
    if len(takes) != 1:
        return  # 30 s and 60 s episodes get theirs from `join`
    record = latest_finish_record(desk, episode, takes[0].take_id)
    final = (
        record.resolve(desk, "final")
        if record is not None and record.complete
        else None
    )
    if final is None or not final.is_file():
        return
    staged = final.with_name(f".{final.stem}-cover-frame.mp4")
    try:
        staged.unlink(missing_ok=True)
        put_cover_on_first_frame(final, result.cover, staged)
        os.replace(staged, final)
    except (MediaToolError, OSError) as exc:
        staged.unlink(missing_ok=True)
        print(
            f"⚠ cover frame skipped on {final.name}: {type(exc).__name__}: {str(exc)[-200:]}",
            file=out,
        )
        return
    print(
        f"Final video {final.name}: the reel's cover {result.cover.name} is its first frame "
        "(the preview Discord and phones show)",
        file=out,
    )
