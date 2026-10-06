"""On-screen hook line and the letterbox title bar: one overlay module, two modes.

Founder decisions of 5 Oct 2026 (fictora-drama ``episode_openings``):

* **hook** (portrait shows, the main path). The episode's selected hook line
  (``episode_summaries[].hook_line_selected.text``; nothing when ``kind`` is
  ``off``) is burned in large white bold type with a heavy outline over the
  first ~3 s of the finished episode (its first take, at ``finish``) and of
  every reel. It sits in the upper safe band just below the top 8% UI strip,
  kept out of the right-hand rail, on at most two lines. When a face sits in
  the upper band it moves to just above the caption band. It leaves at the
  first cut after ~3 s (2.5-4 s), else with a quick fade at 3 s. It is skipped
  when it says the same words as the episode's first spoken line.
* **title_bar** (only when the show's ``delivery_format`` is ``letterbox``).
  While 4:3 takes are not available yet, a solid band runs across the top,
  just below the UI strip, for the whole video on the full portrait picture,
  with the series title and the hook line (or a creator-set ``title_line``).
  Captions are untouched. A take that really is 4:3 gets nothing here: the
  server's letterbox delivery already puts the title in its band.

With no hook line (or ``--no-hook-line``) on a portrait show nothing is drawn
and finish and reel run exactly the commands they always ran
(``tests/test_hook_overlay_portrait_unchanged.py``).

Assumptions and dependencies:

* ``delivery_format`` is read from the spine's top level (``portrait`` |
  ``letterbox``), else from a ``delivery`` or ``show`` object; absent means
  portrait. Another session adds the field server-side.
* The face signal is the small :data:`FaceInUpperBand` interface. The default
  is :func:`creation.post.faces.upper_band_face` (anime and real face
  detectors); when no detector can run it answers ``None`` (unknown) and the
  line stays at the top.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from creation.caption_dashes import drawn_text as no_dash_text
from creation.captions import (
    CAPTION_BAND,
    FONT_NAME,
    HOUSE_CANVAS_HEIGHT,
    _ass_escape,
    _ass_time,
    text_width,
)

#: Seconds the hook line aims to stay on screen.
HOOK_SECONDS = 3.0
#: It leaves at the first cut in this window; with no cut there, it fades out at ``HOOK_SECONDS``.
HOOK_CUT_WINDOW = (2.5, 4.0)
#: Fade in / out, milliseconds.
HOOK_FADE_MS = (120, 250)
#: Top UI strip (tabs, search) and the right-hand rail (like, comment, share), as frame fractions.
TOP_STRIP = 0.08
RIGHT_RAIL_X = 0.88
#: Gap under the top strip, and above the caption band, as a fraction of frame height.
BAND_GAP = 0.015
#: Hook line size on the 1920-high canvas (the house caption is 64): large, bold.
HOOK_FONT_SIZE = 88
HOOK_OUTLINE = 7
HOOK_SHADOW = 3
#: Smallest size a long hook line shrinks to before it wraps past two lines.
HOOK_MIN_FONT_SIZE = 56
#: Left margin on the 1080-wide canvas; the right margin also clears the rail.
SIDE_MARGIN = 60
#: Title bar: band height (fraction of frame height) and its two text sizes on 1920.
BAR_HEIGHT = 0.12
BAR_TITLE_SIZE = 44
BAR_LINE_SIZE = 60
#: A take whose picture is 4:3 within this tolerance is a real letterbox take.
FOUR_THREE = 4 / 3
ASPECT_TOLERANCE = 0.04

DeliveryFormat = Literal["portrait", "letterbox"]
Placement = Literal["top", "lower", "bar"]
#: ``(video, start_s, end_s)`` -> a face sits in the upper band there (True / False), or None when unknown.
FaceInUpperBand = Callable[[Path, float, float], "bool | None"]

_NOT_WORD = re.compile(r"[^\w\s]+")


def same_words(left: str, right: str) -> bool:
    """True when two lines say the same words, ignoring case and punctuation."""

    def words(text: str) -> list[str]:
        return _NOT_WORD.sub(" ", text.casefold()).split()

    return bool(words(left)) and words(left) == words(right)


def delivery_format(spine: Mapping[str, Any] | None) -> DeliveryFormat:
    """The show's delivery format: ``letterbox`` only when the spine says so, else ``portrait``."""

    if not isinstance(spine, Mapping):
        return "portrait"
    for holder in (spine, spine.get("delivery"), spine.get("show")):
        if isinstance(holder, Mapping):
            value = holder.get("delivery_format") or (
                holder.get("format") if holder is not spine else None
            )
            if isinstance(value, str) and value.strip().casefold() == "letterbox":
                return "letterbox"
    return "portrait"


def _summary(spine: Mapping[str, Any], episode: int) -> Mapping[str, Any]:
    from creation.spine_view import episode_summary

    return episode_summary(spine, episode)


def selected_hook_line(spine: Mapping[str, Any] | None, episode: int) -> str | None:
    """The hook line the creator selected for an episode (None when off or none drafted)."""

    if not isinstance(spine, Mapping):
        return None
    selected = _summary(spine, episode).get("hook_line_selected")
    if not isinstance(selected, Mapping) or selected.get("kind") == "off":
        return None
    text = " ".join(str(selected.get("text") or "").split())
    return text or None


def first_spoken_line(spine: Mapping[str, Any] | None, episode: int) -> str | None:
    """The episode's first spoken line: its lowest beat with a line, else the summary's ``first_line``."""

    if not isinstance(spine, Mapping):
        return None
    summary = _summary(spine, episode)
    episode_id = summary.get("episode_id")
    beats = sorted(
        (
            b
            for b in spine.get("beats") or []
            if isinstance(b, Mapping) and b.get("episode_id") == episode_id
        ),
        key=lambda b: int(b.get("ordinal") or 0),
    )
    for beat in beats:
        for line in beat.get("dialogue_lines") or []:
            text = (
                str(line.get("text") or "").strip() if isinstance(line, Mapping) else ""
            )
            if text:
                return text
    line = summary.get("first_line")
    return str(line).strip() or None if line else None


def hook_end(
    cuts: Sequence[float], *, duration: float | None = None
) -> tuple[float, bool]:
    """When the hook line leaves: the first cut in ``HOOK_CUT_WINDOW``, else ``HOOK_SECONDS`` with a fade.

    Returns
    -------
    tuple[float, bool]
        The end in seconds, and whether it leaves on a cut (no fade out).
    """

    low, high = HOOK_CUT_WINDOW
    on_cut = sorted(c for c in cuts if low <= c <= high)
    end, cut = (on_cut[0], True) if on_cut else (HOOK_SECONDS, False)
    if duration is not None and duration > 0:
        end = min(end, duration)
    return round(end, 3), cut


@dataclass(frozen=True)
class HookOverlay:
    """What the overlay draws, where and when; ``as_json`` is what plan files record."""

    mode: Literal["hook", "title_bar"]
    text: str
    placement: Placement
    start_s: float
    end_s: float | None
    on_cut: bool = False
    title: str = ""
    source: str = "spine"

    def as_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "text": self.text,
            "title": self.title or None,
            "placement": self.placement,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "leaves": "whole video"
            if self.end_s is None
            else ("on a cut" if self.on_cut else "fade"),
            "source": self.source,
        }

    def describe(self) -> str:
        if self.mode == "title_bar":
            return f"letterbox title bar for the whole video: {self.title!r} / {self.text!r}"
        where = (
            "top, under the UI strip"
            if self.placement == "top"
            else "just above the captions (a face is up top)"
        )
        leaves = "on the cut" if self.on_cut else "with a fade"
        return f"hook line {self.text!r} {self.start_s:.1f}-{self.end_s:.1f} s, {where}, leaves {leaves}"


@dataclass(frozen=True)
class HookDecision:
    """The overlay to draw, or why nothing is drawn."""

    overlay: HookOverlay | None
    skipped: str | None = None

    def as_json(self) -> dict[str, Any]:
        if self.overlay is None:
            return {"mode": None, "skipped": self.skipped}
        return {**self.overlay.as_json(), "skipped": None}

    def line(self) -> str:
        return (
            self.overlay.describe() if self.overlay else f"no hook line: {self.skipped}"
        )


def default_face_in_upper_band(
    video: Path, start_s: float, end_s: float
) -> bool | None:
    """Ask :func:`creation.post.faces.upper_band_face` whether a face sits where the line goes.

    The band runs from under the top UI strip down to the caption band. None
    when no detector can run (the line stays at the top, and the plan says why).
    """

    from creation.post.faces import upper_band_face

    try:
        answer = upper_band_face(
            video, start_s, end_s, top=TOP_STRIP, bottom=CAPTION_BAND[0]
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            f"!! face check failed ({type(exc).__name__}: {exc}); the hook line stays at the top"
        )
        return None
    return None if answer is None else bool(answer)


def is_four_three(width: int, height: int) -> bool:
    """True when the picture is 4:3 (within ``ASPECT_TOLERANCE``)."""

    return height > 0 and abs(width / height - FOUR_THREE) <= ASPECT_TOLERANCE


def decide(
    spine: Mapping[str, Any] | None,
    episode: int,
    *,
    override: str | None = None,
    off: bool = False,
    position: Literal["top", "lower"] | None = None,
    cuts: Sequence[float] = (),
    duration: float | None = None,
    size: tuple[int, int] | None = None,
    video: Path | None = None,
    face_in_upper_band: FaceInUpperBand | None = default_face_in_upper_band,
    series_title: str | None = None,
) -> HookDecision:
    """Decide the overlay for one episode's finished video or reel.

    Parameters
    ----------
    spine
        Spine JSON (``GET /v1/spines/{id}``).
    episode
        Episode ordinal.
    override
        ``--hook-line TEXT``: the operator's line instead of the spine's.
    off
        ``--no-hook-line``: draw nothing (hook mode and title bar).
    position
        ``--hook-line-position``: force ``top`` or ``lower``.
    cuts
        Cut times (seconds) in the video, for leaving on a cut.
    duration
        Video length.
    size
        ``(width, height)`` of the video; a 4:3 take on a letterbox show is left to the server.
    video
        The video, for the face check.
    face_in_upper_band
        Face signal (:data:`FaceInUpperBand`); None skips it.
    series_title
        The series title for the letterbox bar (default the spine's ``title``).

    Returns
    -------
    HookDecision
        The overlay, or the reason none is drawn.
    """

    if off:
        return HookDecision(None, "turned off (--no-hook-line)")
    fmt = delivery_format(spine)
    spoken = first_spoken_line(spine, episode)
    text = (
        " ".join(override.split())
        if override and override.strip()
        else selected_hook_line(spine, episode)
    )
    source = "--hook-line" if override and override.strip() else "spine"
    if fmt == "letterbox":
        if size is not None and is_four_three(*size):
            return HookDecision(
                None,
                "letterbox show on a 4:3 take: the server's letterbox delivery carries the title",
            )
        summary = _summary(spine, episode) if isinstance(spine, Mapping) else {}
        title_line = (
            " ".join(str(summary.get("title_line") or "").split()) or text or ""
        )
        title = series_title or (
            str(spine.get("title") or "") if isinstance(spine, Mapping) else ""
        )
        if not title_line and not title:
            return HookDecision(
                None, "letterbox show with no series title and no hook line"
            )
        return HookDecision(
            HookOverlay(
                "title_bar", title_line, "bar", 0.0, None, title=title, source=source
            )
        )
    if not text:
        return HookDecision(
            None, "the episode has no hook line selected (off, or none drafted)"
        )
    if spoken and same_words(text, spoken):
        return HookDecision(
            None,
            f"it says the first spoken line ({spoken!r}), so the screen would repeat it",
        )
    end, on_cut = hook_end(cuts, duration=duration)
    placement: Placement = "top"
    if position is not None:
        placement = position
    elif face_in_upper_band is not None and video is not None:
        if face_in_upper_band(video, 0.0, end):
            placement = "lower"
    return HookDecision(
        HookOverlay("hook", text, placement, 0.0, end, on_cut=on_cut, source=source)
    )


def _scaled(value: float, height: int) -> int:
    return max(8, round(value * height / HOUSE_CANVAS_HEIGHT))


def _fit(text: str, size: int, room: float, floor: int) -> tuple[list[str], int]:
    """Lay ``text`` on at most two balanced lines inside ``room`` px, shrinking no lower than ``floor``."""

    if text_width(text, size) <= room:
        return [text], size
    words = text.split()
    best: tuple[float, list[str]] | None = None
    for cut in range(1, len(words)):
        top, bottom = " ".join(words[:cut]), " ".join(words[cut:])
        widest = max(text_width(top, size), text_width(bottom, size))
        if best is None or widest < best[0]:
            best = (widest, [top, bottom])
    if best is None:
        return [text], max(floor, int(size * room / text_width(text, size)))
    widest, lines = best
    return lines, size if widest <= room else max(floor, int(size * room / widest))


def overlay_ass(
    overlay: HookOverlay, *, width: int, height: int, duration: float | None = None
) -> str:
    """The ASS that draws ``overlay`` on a ``width`` x ``height`` video.

    The text block is centred between the left margin and the right-hand rail
    (``RIGHT_RAIL_X``), never under the top strip. Hook mode: white bold, heavy
    black outline and shadow. Title bar: a solid dark band with white text.
    """

    # No em or en dash on the picture (6 Oct 2026; creation.caption_dashes).
    overlay = replace(
        overlay, text=no_dash_text(overlay.text), title=no_dash_text(overlay.title)
    )
    left = round(SIDE_MARGIN * width / 1080)
    right = width - round(RIGHT_RAIL_X * width) + left
    room = width - left - right
    centre_x = left + room // 2
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Hook,{FONT_NAME},{_scaled(HOOK_FONT_SIZE, height)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{_scaled(HOOK_OUTLINE, height)},{_scaled(HOOK_SHADOW, height)},8,"
        f"{left},{right},0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    if overlay.mode == "title_bar":
        end = _ass_time(duration if duration else 24 * 3600 - 1)
        top = round((TOP_STRIP + BAND_GAP / 2) * height)
        band = round(BAR_HEIGHT * height)
        title_size = _scaled(BAR_TITLE_SIZE, height)
        title_lines, title_size = _fit(
            overlay.title, title_size, room, max(8, title_size * 2 // 3)
        )
        line_size = _scaled(BAR_LINE_SIZE, height)
        line_lines, line_size = _fit(
            overlay.text, line_size, room, max(8, line_size * 2 // 3)
        )
        plate = f"{{\\an7\\pos(0,{top})\\bord0\\shad0\\1c&H101010&\\1a&H10&\\p1}}m 0 0 l {width} 0 {width} {band} 0 {band}{{\\p0}}"
        events = [f"Dialogue: 0,{_ass_time(0)},{end},Hook,,0,0,0,,{plate}\n"]
        text = "\\N".join(
            [f"{{\\fs{title_size}}}" + "\\N".join(_ass_escape(x) for x in title_lines)]
            if overlay.title
            else []
        )
        if overlay.text:
            text += (
                ("\\N" if text else "")
                + f"{{\\fs{line_size}}}"
                + "\\N".join(_ass_escape(x) for x in line_lines)
            )
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{end},Hook,,0,0,0,,"
            f"{{\\an5\\pos({centre_x},{top + band // 2})\\bord0\\shad0}}{text}\n"
        )
        return header + "".join(events)
    size = _scaled(HOOK_FONT_SIZE, height)
    lines, size = _fit(overlay.text, size, room, _scaled(HOOK_MIN_FONT_SIZE, height))
    fade_in, fade_out = HOOK_FADE_MS
    fade = f"\\fad({fade_in},{0 if overlay.on_cut else fade_out})"
    if overlay.placement == "lower":
        anchor = (
            f"\\an2\\pos({centre_x},{round((CAPTION_BAND[0] - BAND_GAP) * height)})"
        )
    else:
        anchor = f"\\an8\\pos({centre_x},{round((TOP_STRIP + BAND_GAP) * height)})"
    sized = f"\\fs{size}" if size != _scaled(HOOK_FONT_SIZE, height) else ""
    body = "\\N".join(_ass_escape(line) for line in lines)
    return header + (
        f"Dialogue: 0,{_ass_time(overlay.start_s)},{_ass_time(overlay.end_s or HOOK_SECONDS)},Hook,,0,0,0,,"
        f"{{{anchor}{fade}{sized}}}{body}\n"
    )


def burn(
    overlay: HookOverlay,
    video: Path,
    ass_path: Path,
    out: Path,
    *,
    duration: float | None = None,
) -> Path:
    """Write the overlay's ASS to ``ass_path`` and burn it onto ``video`` as ``out`` (house fonts, libass)."""

    from creation.captions import burn_ass, find_ffmpeg
    from creation.post.media import probe_video

    info = probe_video(video)
    ass_path.write_text(
        overlay_ass(
            overlay,
            width=info.width,
            height=info.height,
            duration=duration or info.duration_seconds,
        ),
        encoding="utf-8",
    )
    burn_ass(find_ffmpeg()[0], video, ass_path, out)
    return out


__all__ = [
    "HOOK_CUT_WINDOW",
    "HOOK_SECONDS",
    "RIGHT_RAIL_X",
    "TOP_STRIP",
    "FaceInUpperBand",
    "HookDecision",
    "HookOverlay",
    "burn",
    "decide",
    "default_face_in_upper_band",
    "delivery_format",
    "first_spoken_line",
    "hook_end",
    "is_four_three",
    "overlay_ass",
    "same_words",
    "selected_hook_line",
]
