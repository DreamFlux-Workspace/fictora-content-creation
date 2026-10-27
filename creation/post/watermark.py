"""The Sokii mark, top left, where the server says (fictora-drama #1039; position 24 Oct 2026).

The server owns the mark's place (server-heavy rule, 15 Oct 2026): top left,
just under the strip TikTok / Reels / Shorts cover, 3% in from the left, as on
the Instagram reels the founder approved (``Final series/Instagram Reels``;
fictora-drama #1047: the 12% the server used for a day was only for the app's
full-bleed Watch feed, which no longer shows a mark). :func:`mark_position`
asks ``POST /v1/mark/placement`` for each frame. An older server without the
route (404/405), or one that cannot be reached, is never an error: the kit uses
the same numbers itself (``x = round(W * 0.03)``, ``y = round(H * 0.09)``,
23:121 on 768x1344) and prints a ``NOTE``. Drawn at 9.4% of the frame's width
(the cat and the word "sokii" as the app's header logo, no pill, since 27 Oct 2026, founder pick "C":
230x74 on 1080), at full strength (its soft shadow is in the PNG). A ``y`` override
is never allowed into the top 8% of the frame: it is raised to that strip's
lower edge.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from creation.post.media import probe_video, run_ffmpeg

MARK = Path(__file__).resolve().parents[2] / "assets" / "sokii-mark-word.png"
#: The PNG's own size (twice its 230x74 size on a 1080-wide frame), the server's ``MARK_PNG_SIZE``.
MARK_PNG_SIZE = (460, 148)
#: The server's route (fictora-drama #1039).
MARK_PLACEMENT_ROUTE = "/v1/mark/placement"
#: Fallback only (an older or unreachable server): the server's
#: ``social_safe_zones.SAVED_COPY_MARK_LEFT_FRACTION`` (3%, the approved reels').
MARK_X_FRACTION = 0.03
#: The mark's width as a share of the frame's (the server's ``MARK_WIDTH_FRACTION``).
MARK_WIDTH_FRACTION = 230 / 1080
MARK_Y_FRACTION = 0.09
SAFE_TOP_FRACTION = 0.08
MARK_ALPHA = 1.0


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


@dataclass(frozen=True)
class LetterboxPlace:
    """Where the server puts the mark and the title block on a letterbox canvas (fictora-drama #1049).

    ``title_bottom`` is the title block's bottom anchor (the server's ASS ``\\pos`` y: 448 on 1920,
    512 under the mark for a show stamped ``letterbox_part_titles``); ``title_four_line_size`` caps a
    four-line block's size under the mark. Both are ``None`` on an older server.
    """

    x: int
    y: int
    title_bottom: int | None = None
    title_four_line_size: int | None = None


def server_letterbox_place(
    width: int,
    height: int,
    *,
    desk: Path | None = None,
    episode: int = 1,
    spine_id: str | None = None,
) -> LetterboxPlace | None:
    """The server's mark and title places on a letterbox canvas, or ``None`` (no desk, older server, no connection).

    The server decides the title's place from the show's stamp (server-heavy rule, 15 Oct 2026), so
    the kit's bands match the app's. Tests answer it with ``creation.caption_server.server_override``.
    """

    from creation.caption_server import _OVERRIDE, _post

    if desk is None and _OVERRIDE.get() is None:
        return None
    body: dict[str, object] = {"width": width, "height": height, "letterbox": True}
    if spine_id:
        body["spine_id"] = spine_id
    try:
        status, answer = _post(desk, episode, MARK_PLACEMENT_ROUTE, body)
    except (OSError, httpx.HTTPError, SystemExit) as exc:
        print(
            f"NOTE: the server could not be asked where the mark and title go ({exc}); the kit placed them",
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

    def number(key: str) -> int | None:
        value = answer.get(key)
        return (
            int(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else None
        )

    try:
        return LetterboxPlace(
            int(answer["x"]),
            int(answer["y"]),
            number("title_bottom"),
            number("title_four_line_size"),
        )
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


def mark_scale_filter(width: int) -> str:
    """The ffmpeg filter that draws the mark at :data:`MARK_WIDTH_FRACTION` of ``width`` (empty at its own size)."""

    drawn = round(width * MARK_WIDTH_FRACTION)
    png_w, png_h = MARK_PNG_SIZE
    return (
        ""
        if drawn == png_w
        else f"scale={drawn}:{round(png_h * drawn / png_w)}:flags=lanczos,"
    )


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
    graph = f"[1:v]{mark_scale_filter(info.width)}format=rgba,colorchannelmixer=aa={MARK_ALPHA}[m];[0:v][m]overlay={x}:{top}[v]"
    inputs = ["-i", str(video), "-i", str(mark)]
    args = [*inputs, "-filter_complex", graph, "-map", "[v]"]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    run_ffmpeg(
        [*args, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    )
    return out
