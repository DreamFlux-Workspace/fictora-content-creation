"""The Sokii mark, top left, where the server says (fictora-drama #1039, 24 Oct 2026).

The server owns the mark's place (server-heavy rule, 15 Oct 2026): top left,
just under the strip TikTok / Reels / Shorts cover, and 12% in from the left so
the app's full-bleed Watch feed on a 19.5:9 or 20:9 phone (which hides up to
10% of each side) never clips it. :func:`mark_position` asks
``POST /v1/mark/placement`` for each frame. An older server without the route
(404/405), or one that cannot be reached, is never an error: the kit uses the
same numbers itself (``x = round(W * 0.12)``, ``y = round(H * 0.09)``, 92:121 on
768x1344) and prints a ``NOTE``. Native size, 0.6 opacity. A ``y`` override is
never allowed into the top 8% of the frame: it is raised to that strip's lower
edge.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from pathlib import Path

import httpx

from creation.post.media import probe_video, run_ffmpeg

MARK = Path(__file__).resolve().parents[2] / "assets" / "sokii-line-72.png"
#: The server's route (fictora-drama #1039).
MARK_PLACEMENT_ROUTE = "/v1/mark/placement"
#: Fallback only (an older or unreachable server): the server's
#: ``social_safe_zones.MARK_LEFT_FRACTION`` (12%; 3% before 24 Oct 2026).
MARK_X_FRACTION = 0.12
MARK_Y_FRACTION = 0.09
SAFE_TOP_FRACTION = 0.08
MARK_ALPHA = 0.6


def server_mark_position(
    width: int,
    height: int,
    *,
    y: int | None = None,
    letterbox: bool = False,
    desk: Path | None = None,
    episode: int = 1,
) -> tuple[int, int] | None:
    """The server's place for the mark on this frame, or ``None`` (no desk, older server, no connection).

    Tests answer it with ``creation.caption_server.server_override``.
    """

    from creation.caption_server import _OVERRIDE, _post

    if desk is None and _OVERRIDE.get() is None:
        return None
    body: dict[str, object] = {"width": width, "height": height, "letterbox": letterbox}
    if y is not None:
        body["y"] = y
    try:
        status, answer = _post(desk, episode, MARK_PLACEMENT_ROUTE, body)
    except (OSError, httpx.HTTPError, SystemExit) as exc:
        print(
            f"NOTE: the server could not be asked where the mark goes ({exc}); the kit placed it",
            file=sys.stderr,
        )
        return None
    if not 200 <= status < 300 or not isinstance(answer, Mapping):
        if status not in (404, 405):
            print(
                f"NOTE: the server did not place the mark (HTTP {status}); the kit placed it",
                file=sys.stderr,
            )
        return None
    try:
        return int(answer["x"]), int(answer["y"])
    except (KeyError, TypeError, ValueError):
        return None


def mark_position(
    width: int,
    height: int,
    *,
    y: int | None = None,
    desk: Path | None = None,
    episode: int = 1,
) -> tuple[int, int]:
    """Overlay ``(x, y)`` for the mark: the server's place, else the same numbers kept here.

    An override ``y`` is clamped below the covered top strip.
    """

    served = server_mark_position(width, height, y=y, desk=desk, episode=episode)
    if served is not None:
        return served
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
    desk: Path | None = None,
    episode: int = 1,
) -> Path:
    """Put the mark on ``video`` into ``out`` (audio copied); the un-marked file stays.

    ``desk`` / ``episode`` reach the server for the mark's place.

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
    x, top = mark_position(info.width, info.height, y=y, desk=desk, episode=episode)
    graph = f"[1:v]format=rgba,colorchannelmixer=aa={MARK_ALPHA}[m];[0:v][m]overlay={x}:{top}[v]"
    inputs = ["-i", str(video), "-i", str(mark)]
    args = [*inputs, "-filter_complex", graph, "-map", "[v]"]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    run_ffmpeg(
        [*args, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    )
    return out
