"""The Sokii mark, top left, where the server says (fictora-drama #1039, #1133).

The server owns the mark's place and size (server-heavy rule, 15 Oct 2026):
since fictora-drama #1133 (founder, 28 Oct 2026) 12% in from the left and 14.5%
down on a portrait frame, clear of what Instagram on a tall iPhone crops (about
9% off each side) and its back-arrow row (about 8.6-11.5% down); on a letterbox
canvas, inside the 4:3 picture, top left. :func:`mark_place`
asks ``POST /v1/mark/placement`` for each frame: the mark's top-left corner, its
drawn size (``mark_width`` x ``mark_height``, 207x67 on 1080x1920 since
fictora-drama #1133, 28 Oct 2026) and, on a portrait frame, where a top hook
line starts (``hook_top``, under the mark). The kit only draws what it is told.
An older server (404/405, or an answer without the new fields), or one that
cannot be reached, is never an error: the kit uses the same numbers itself
(12% in, 14.5% down, 207/1080 of the width, the hook line 2.5% under the mark:
130:278, 207x67, hook at 393 on 1080x1920; named constants below, each naming
the server constant it mirrors) and prints a ``NOTE`` when the server could not
be asked. The mark is drawn at full strength (its soft shadow
is in the PNG). A ``y`` override is never allowed into the top 8% of the frame:
it is raised to that strip's lower edge.
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
#: ``social_safe_zones.SAVED_COPY_MARK_LEFT_FRACTION``, the mark's left edge on portrait and letterbox:
#: 12% in since fictora-drama #1133 (founder, 28 Oct 2026: Instagram on a tall iPhone crops about 9% off
#: each side; 3% before).
MARK_X_FRACTION = 0.12
#: Fallback only: the server's ``social_safe_zones.MARK_WIDTH_FRACTION``, the mark's drawn width as a share
#: of the frame's (207x67 on 1080 since fictora-drama #1133, 28 Oct 2026: 10% smaller than 230x74).
MARK_WIDTH_FRACTION = 207 / 1080
#: Fallback only: the server's ``social_safe_zones.MARK_TOP_FRACTION``, the portrait mark's top edge:
#: 14.5% down since fictora-drama #1133 (under Instagram's back-arrow row, about 8.6-11.5%; 9% before).
MARK_Y_FRACTION = 0.145
#: Fallback only: the server's gap from the mark's bottom to a portrait top hook line's top
#: (``hook_top``, fictora-drama #1133), as a share of the frame's height: 393 on 1080x1920.
HOOK_GAP_FRACTION = 0.025
SAFE_TOP_FRACTION = 0.08
MARK_ALPHA = 1.0


@dataclass(frozen=True)
class MarkPlace:
    """Where the mark goes on a frame and how big it is drawn, and where a portrait top hook line starts.

    ``x``/``y`` is the mark's top-left corner (ffmpeg ``overlay=x:y``); ``width`` x ``height`` its
    drawn size; ``hook_top`` the top of a "top" hook line (:mod:`creation.post.hook_overlay`).
    """

    x: int
    y: int
    width: int
    height: int
    hook_top: int


def _number(answer: Mapping[str, object], key: str) -> int | None:
    value = answer.get(key)
    return (
        int(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


def fallback_mark_place(width: int, height: int, *, y: int | None = None) -> MarkPlace:
    """The kit's own copy of the server's place (older or unreachable server): 130:278, 207x67, hook at 393 on 1080x1920."""

    top = (
        round(height * MARK_Y_FRACTION)
        if y is None
        else max(y, math.ceil(height * SAFE_TOP_FRACTION))
    )
    drawn_w, drawn_h = mark_size(width)
    return MarkPlace(
        round(width * MARK_X_FRACTION), top, drawn_w, drawn_h,
        top + drawn_h + round(height * HOOK_GAP_FRACTION),
    )  # fmt: skip


def server_mark_place(
    width: int,
    height: int,
    *,
    y: int | None = None,
    letterbox: bool = False,
    desk: Path | None = None,
    episode: int = 1,
) -> MarkPlace | None:
    """The server's place and size for the mark on this frame, or ``None`` (no desk, older server, no connection).

    An answer without ``mark_width``/``mark_height`` (an older server) keeps the kit's size; one
    without ``hook_top`` starts the hook line the kit's gap under the mark. Tests answer it with
    ``creation.caption_server.server_override``.
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
    x, top = _number(answer, "x"), _number(answer, "y")
    if x is None or top is None:
        return None
    served_w, served_h = _number(answer, "mark_width"), _number(answer, "mark_height")
    drawn_w, drawn_h = (
        (served_w, served_h)
        if served_w and served_h and served_w > 0 and served_h > 0
        else mark_size(width)
    )
    hook_top = _number(answer, "hook_top")
    if hook_top is None:
        hook_top = top + drawn_h + round(height * HOOK_GAP_FRACTION)
    return MarkPlace(x, top, drawn_w, drawn_h, hook_top)


def server_mark_position(
    width: int,
    height: int,
    *,
    y: int | None = None,
    letterbox: bool = False,
    desk: Path | None = None,
    episode: int = 1,
) -> tuple[int, int] | None:
    """The server's ``(x, y)`` for the mark on this frame, or ``None`` (:func:`server_mark_place`)."""

    place = server_mark_place(
        width, height, y=y, letterbox=letterbox, desk=desk, episode=episode
    )
    return None if place is None else (place.x, place.y)


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
    #: The mark's drawn size (fictora-drama #1133); ``None`` on an older server (the kit's size).
    mark_width: int | None = None
    mark_height: int | None = None


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

    x, top = _number(answer, "x"), _number(answer, "y")
    if x is None or top is None:
        return None
    served_w, served_h = _number(answer, "mark_width"), _number(answer, "mark_height")
    sized = bool(served_w and served_h and served_w > 0 and served_h > 0)
    return LetterboxPlace(
        x, top, _number(answer, "title_bottom"), _number(answer, "title_four_line_size"),
        served_w if sized else None, served_h if sized else None,
    )  # fmt: skip


def mark_place(
    width: int,
    height: int,
    *,
    y: int | None = None,
    desk: Path | None = None,
    episode: int = 1,
) -> MarkPlace:
    """The mark's place and size on a portrait frame: the server's, else the same numbers kept here.

    An override ``y`` is clamped below the covered top strip.
    """

    served = server_mark_place(width, height, y=y, desk=desk, episode=episode)
    return served if served is not None else fallback_mark_place(width, height, y=y)


def mark_position(
    width: int,
    height: int,
    *,
    y: int | None = None,
    desk: Path | None = None,
    episode: int = 1,
) -> tuple[int, int]:
    """Overlay ``(x, y)`` for the mark (:func:`mark_place`)."""

    place = mark_place(width, height, y=y, desk=desk, episode=episode)
    return place.x, place.y


def mark_size(width: int) -> tuple[int, int]:
    """The kit's own drawn ``(width, height)`` for the mark on a ``width``-wide frame (207x67 on 1080)."""

    drawn = round(width * MARK_WIDTH_FRACTION)
    png_w, png_h = MARK_PNG_SIZE
    return drawn, round(png_h * drawn / png_w)


def mark_scale_filter(drawn: tuple[int, int]) -> str:
    """The ffmpeg filter that draws the mark at ``drawn`` (width, height); empty at the PNG's own size."""

    if drawn == MARK_PNG_SIZE:
        return ""
    return f"scale={drawn[0]}:{drawn[1]}:flags=lanczos,"


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
    place = mark_place(info.width, info.height, y=y, desk=desk, episode=episode)
    scale = mark_scale_filter((place.width, place.height))
    graph = f"[1:v]{scale}format=rgba,colorchannelmixer=aa={MARK_ALPHA}[m];[0:v][m]overlay={place.x}:{place.y}[v]"
    inputs = ["-i", str(video), "-i", str(mark)]
    args = [*inputs, "-filter_complex", graph, "-map", "[v]"]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    run_ffmpeg(
        [*args, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    )
    return out
