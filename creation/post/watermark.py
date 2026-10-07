"""The Sokii mark, top left, just under the strip TikTok / Reels / Shorts cover.

Position ``x = round(W * 0.03)``, ``y = round(H * 0.09)`` (23:121 on 768x1344),
native size, 0.6 opacity. A ``y`` override is never allowed into the top 8% of
the frame: it is raised to that strip's lower edge.
"""

from __future__ import annotations

import math
from pathlib import Path

from creation.post.media import probe_video, run_ffmpeg

MARK = Path(__file__).resolve().parents[2] / "assets" / "sokii-line-72.png"
MARK_X_FRACTION = 0.03
MARK_Y_FRACTION = 0.09
SAFE_TOP_FRACTION = 0.08
MARK_ALPHA = 0.6


def mark_position(width: int, height: int, *, y: int | None = None) -> tuple[int, int]:
    """Overlay ``(x, y)`` for the mark; an override ``y`` is clamped below the covered top strip."""

    top = (
        round(height * MARK_Y_FRACTION)
        if y is None
        else max(y, math.ceil(height * SAFE_TOP_FRACTION))
    )
    return round(width * MARK_X_FRACTION), top


def watermark(
    video: Path,
    out: Path,
    *,
    y: int | None = None,
    mark: Path = MARK,
    cover: Path | None = None,
) -> Path:
    """Put the mark on ``video`` into ``out`` (audio copied); the un-marked file stays.

    ``cover`` becomes the first frame in the same encode (:mod:`creation.post.cover_frame`).

    Raises
    ------
    FileNotFoundError
        When the mark image is missing.
    FileExistsError
        When ``out`` exists.
    """

    if not mark.is_file():
        raise FileNotFoundError(f"watermark not found: {mark}")
    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    info = probe_video(video)
    x, top = mark_position(info.width, info.height, y=y)
    graph = f"[1:v]format=rgba,colorchannelmixer=aa={MARK_ALPHA}[m];[0:v][m]overlay={x}:{top}[v]"
    inputs = ["-i", str(video), "-i", str(mark)]
    picture = "[v]"
    if cover is not None:
        from creation.post.cover_frame import cover_frame_graph

        inputs += ["-i", str(cover)]
        graph += ";" + cover_frame_graph(
            cover_input=2,
            picture="[v]",
            width=info.width,
            height=info.height,
            out="[vc]",
        )
        picture = "[vc]"
    args = [*inputs, "-filter_complex", graph, "-map", picture]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    run_ffmpeg(
        [*args, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    )
    return out
