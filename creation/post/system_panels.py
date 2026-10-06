"""System panels on a finished take: the status windows a system or game genre is told through.

The kit's twin of fictora-drama ``system_panels.py`` (placement) and
``system_panel_render.py`` (the ASS layer); the kit reads the spine through the
API and never imports the server, so the code is mirrored here. Keep the two in
step: a rule change in one is a change in the other.

* **Genres** (user decision, 6 Oct 2026): only ``system_leveling``,
  ``last_human``, ``isekai``, ``cultivation`` and ``regression_revenge``
  (primary or overlay) ever show a panel.
* **The writer decides** each panel on a beat (``beats[].system_panels``): its
  form, words, numbers, look (``style``, else the show's ``system_panel_look``)
  and position. Nothing here is a template, and the operator is never asked.
* **Placement**: a beat's panel starts at its even share of its take plus
  ``appear_at_ms`` (or at the take's start with ``anchor: take``), never
  outlives the take; at most two in a take, never the same form twice in a
  row, never two at once.
* **Drawing**: the form decides the layout, the frame the shape (glass,
  hologram, neon, ink scroll, parchment, brush), the palette the colours. The
  box opens in 0.18 s, the lines type in, the panel fades out. Inside the
  picture, under the top strip and the mark, above the caption band, out of
  the right rail. Digits are always in the house font.

``finish`` burns the take's panels after its captions (``--no-panels`` skips
them). Reels come from the server's reel engine, which draws them itself.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.captions import CAPTION_BAND, FONT_NAME, HOUSE_CANVAS_HEIGHT, text_width

_LOG = logging.getLogger(__name__)

#: The genres a system panel belongs to.
SYSTEM_PANEL_GENRES = frozenset(
    {"system_leveling", "last_human", "isekai", "cultivation", "regression_revenge"}
)
#: Platform zones (fictora-drama ``social_safe_zones``): top strip and right rail.
SAFE_TOP_FRACTION = 0.08
SAFE_RIGHT_RAIL_WIDTH_FRACTION = 0.12
CAPTION_BAND_TOP_FRACTION = CAPTION_BAND[0]
#: A panel is on screen this long.
MIN_SYSTEM_PANEL_MS = 1200
MAX_SYSTEM_PANEL_MS = 3000
MAX_SYSTEM_PANELS_PER_TAKE = 2
#: Two panels never overlap: a later one waits this long after the earlier one ends.
PANEL_GAP_MS = 150
#: A take whose length is unknown counts as this long when placing later takes' panels.
NOMINAL_TAKE_MS = 15_000

_LONG_DASHES = re.compile(r"\s*[\u2013\u2014]\s*")


def without_long_dashes(text: str) -> str:
    """Replace em and en dashes with a colon (no long dash is ever drawn)."""

    return " ".join(_LONG_DASHES.sub(": ", text).split()).strip(": ").strip()


@dataclass(frozen=True)
class PanelStyle:
    """A panel's frame shape and one to three ``#RRGGBB`` colours (fill, accent, text)."""

    frame: str
    palette: tuple[str, ...]


#: The look a panel gets when neither it nor its show names one: dark glass, cyan accent.
DEFAULT_LOOK = PanelStyle("glass", ("#0B1220", "#5FD4FF", "#F4F8FF"))
_FRAMES = ("glass", "hologram", "ink_scroll", "parchment", "neon", "brush")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class PlacedPanel:
    """One panel placed on the episode's timeline (milliseconds), its look resolved."""

    panel_id: str
    take: int
    start_ms: int
    end_ms: int
    form: str
    lines: tuple[str, ...]
    style: PanelStyle
    position: str = "upper"

    def describe(self) -> str:
        """One line for the run notes."""

        return (
            f"{self.form} ({self.style.frame}, {self.position}) {self.start_ms / 1000:.2f}-"
            f"{self.end_ms / 1000:.2f} s: {' / '.join(self.lines)}"
        )


def show_takes_panels(spine: Mapping[str, Any] | None) -> bool:
    """Whether the show's genre or an overlay is a system or game genre."""

    if not isinstance(spine, Mapping):
        return False
    genres = [spine.get("microdrama_genre"), *(spine.get("microdrama_genre_overlays") or [])]
    return any(g in SYSTEM_PANEL_GENRES for g in genres if isinstance(g, str))


def _style(raw: Any) -> PanelStyle | None:
    if not isinstance(raw, Mapping):
        return None
    frame = raw.get("frame")
    palette = tuple(str(c) for c in raw.get("palette") or [] if _HEX.match(str(c)))[:3]
    if frame not in _FRAMES or not palette:
        return None
    return PanelStyle(str(frame), palette)


def _take_for_beat(ordinal: int, position: int, beat_count: int, pattern: Sequence[int], take_count: int) -> int:
    if pattern and sum(pattern) >= ordinal >= 1:
        cursor = 1
        for index, count in enumerate(pattern, start=1):
            if ordinal < cursor + count:
                return index
            cursor += count
    return min(take_count, (position - 1) * take_count // max(1, beat_count) + 1)


def place_panels(
    spine: Mapping[str, Any] | None, episode_id: str, take_durations_ms: Sequence[int]
) -> list[PlacedPanel]:
    """Place an episode's panels on its takes, back to back (fictora-drama ``place_episode_system_panels``).

    Parameters
    ----------
    spine
        The spine as JSON.
    episode_id
        The episode.
    take_durations_ms
        Each take's length in playing order (``t1`` first); later takes may be left off.

    Returns
    -------
    list[PlacedPanel]
        In time order; empty outside the system and game genres.
    """

    if not show_takes_panels(spine) or not take_durations_ms:
        return []
    assert spine is not None
    durations = [max(0, int(d)) for d in take_durations_ms]
    show_look = _style(spine.get("system_panel_look"))
    beats = sorted(
        (b for b in spine.get("beats") or [] if isinstance(b, Mapping) and b.get("episode_id") == episode_id),
        key=lambda beat: int(beat.get("ordinal") or 0),
    )
    pattern = tuple(int(n) for n in spine.get("beats_per_storyboard_set") or ())
    by_take: dict[int, list[Mapping[str, Any]]] = {}
    for position, beat in enumerate(beats, start=1):
        take = _take_for_beat(int(beat.get("ordinal") or position), position, len(beats), pattern, max(len(pattern), 1))
        by_take.setdefault(take, []).append(beat)
    starts = [sum(durations[:index]) for index in range(len(durations))]
    placed: list[PlacedPanel] = []
    for take in sorted(by_take):
        if take > len(durations):
            break
        members = by_take[take]
        take_start, take_ms = starts[take - 1], durations[take - 1]
        take_end = take_start + take_ms
        in_take = 0
        for index, beat in enumerate(members):
            for number, cue in enumerate(beat.get("system_panels") or [], start=1):
                if not isinstance(cue, Mapping):
                    continue
                lines = tuple(without_long_dashes(str(line)) for line in cue.get("lines") or [] if str(line).strip())[:5]
                form = str(cue.get("form") or "")
                panel_id = f"{beat.get('beat_id') or f'beat{index + 1}'}-panel{number}"
                if not lines or not form:
                    continue
                if in_take >= MAX_SYSTEM_PANELS_PER_TAKE:
                    _LOG.info("system panel %s dropped: take %d already has %d", panel_id, take, in_take)
                    continue
                if placed and placed[-1].form == form:
                    _LOG.info("system panel %s dropped: the same %s straight after another", panel_id, form)
                    continue
                anchor = take_start if cue.get("anchor") == "take" else take_start + take_ms * index // len(members)
                duration = min(MAX_SYSTEM_PANEL_MS, max(MIN_SYSTEM_PANEL_MS, int(cue.get("duration_ms") or 2000)))
                start = anchor + max(0, int(cue.get("appear_at_ms") or 0))
                if placed:
                    start = max(start, placed[-1].end_ms + PANEL_GAP_MS)
                start = min(start, take_end - MIN_SYSTEM_PANEL_MS)
                end = min(start + duration, take_end)
                if start < take_start or end - start < MIN_SYSTEM_PANEL_MS or (placed and start < placed[-1].end_ms):
                    continue
                placed.append(
                    PlacedPanel(
                        panel_id=panel_id,
                        take=take,
                        start_ms=start,
                        end_ms=end,
                        form=form,
                        lines=lines,
                        style=_style(cue.get("style")) or show_look or DEFAULT_LOOK,
                        position=str(cue.get("position") or "upper"),
                    )
                )
                in_take += 1
    return placed


def take_panels(
    spine: Mapping[str, Any] | None,
    episode_id: str,
    take: int,
    lengths_s: Sequence[float | None],
) -> list[tuple[int, int, PlacedPanel]]:
    """Return one take's panels on the take's own timeline, ``(start_ms, end_ms, panel)``.

    Parameters
    ----------
    spine
        The spine as JSON.
    episode_id
        The episode.
    take
        The take's number (``t3`` is 3).
    lengths_s
        Lengths of ``t1`` .. this take in seconds (``None``: unknown, counted as 15 s).

    Returns
    -------
    list[tuple[int, int, PlacedPanel]]
        The take's panels, in order.
    """

    durations = [round(s * 1000) if s else NOMINAL_TAKE_MS for s in lengths_s][:take]
    if len(durations) < take:
        return []
    offset = sum(durations[: take - 1])
    return [
        (p.start_ms - offset, p.end_ms - offset, p)
        for p in place_panels(spine, episode_id, durations)
        if p.take == take
    ]


#: The serif face for the paper and brush frames. libass falls back through
#: fontconfig when it is missing; digits never depend on it (house font).
SERIF_FONT_NAME = "Georgia"
#: Frames set in the serif face.
SERIF_FRAMES = frozenset({"ink_scroll", "parchment", "brush"})

#: Left margin and the gap kept above the caption band, as frame fractions.
SIDE_MARGIN_FRACTION = 0.055
#: The top of the panel area on a full-frame portrait picture: under the top
#: strip (8%) and the Sokii mark that sits just below it.
PANEL_TOP_FRACTION = 0.13
#: A panel ends this far above the caption band (which starts at 55%).
CAPTION_GAP_FRACTION = 0.02
#: A narrow panel (``upper_left``, ``upper_right``) is at most this share of the area's width.
NARROW_SHARE = 0.64

#: The box opens over this long; the lines start typing after it.
OPEN_MS = 180
FADE_IN_MS = 120
FADE_OUT_MS = 220
#: Typing speed: this many milliseconds a character at most, and all lines
#: typed by this share of the panel's time on screen at the latest.
MAX_CHAR_MS = 45
TYPE_SHARE = 0.45

#: Sizes on the 1920-high house canvas.
_SIZES = {"header": 50, "body": 40, "hero": 78, "small": 34, "skill": 62}
_PAD = 30
_RADIUS = 18
_BORDER = 3
_LINE_GAP = 0.32
#: Smallest a panel's text shrinks to fit before it is set anyway.
_MIN_SCALE = 0.62


@dataclass(frozen=True)
class PanelArea:
    """Where panels may sit, in pixels of the play resolution.

    Parameters
    ----------
    left, top, right, bottom
        The area's edges.
    """

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        """The area's width in pixels."""

        return self.right - self.left

    @property
    def height(self) -> int:
        """The area's height in pixels."""

        return self.bottom - self.top


def panel_area(
    width: int,
    height: int,
    *,
    picture: tuple[int, int, int, int] | None = None,
    captions_on_picture: bool = True,
) -> PanelArea:
    """Return the area panels may use on a ``width`` x ``height`` frame.

    Parameters
    ----------
    width, height
        The frame (play resolution) in pixels.
    picture
        The picture's box ``(x, y, w, h)`` when it does not fill the frame (a
        letterbox delivery); ``None`` for a full-frame picture.
    captions_on_picture
        Whether burned captions sit on the picture (portrait full frame), so
        the panel stays above their band.

    Returns
    -------
    PanelArea
        Inside the picture, out of the top strip, the right-hand rail and the
        caption band.
    """

    px, py, pw, ph = picture if picture is not None else (0, 0, width, height)
    portrait = height > width
    inset = round(0.04 * ph)
    left = max(px + inset, round(SIDE_MARGIN_FRACTION * width))
    right = min(px + pw - inset, round((1.0 - SAFE_RIGHT_RAIL_WIDTH_FRACTION) * width) - round(0.01 * width))
    top = py + inset
    bottom = py + ph - inset
    if portrait and picture is None:
        top = max(top, round(max(PANEL_TOP_FRACTION, SAFE_TOP_FRACTION) * height))
        if captions_on_picture:
            bottom = min(bottom, round((CAPTION_BAND_TOP_FRACTION - CAPTION_GAP_FRACTION) * height))
    return PanelArea(left=left, top=top, right=max(left + 1, right), bottom=max(top + 1, bottom))


# --- colour -------------------------------------------------------------------------------------


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _ass_colour(hex_colour: str) -> str:
    r, g, b = _rgb(hex_colour)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _luminance(hex_colour: str) -> float:
    r, g, b = _rgb(hex_colour)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


@dataclass(frozen=True)
class _Colours:
    fill: str
    accent: str
    text: str
    dim: str
    shade: str


def _colours(palette: Sequence[str]) -> _Colours:
    fill = palette[0]
    accent = palette[1] if len(palette) > 1 else palette[0]
    light_fill = _luminance(fill) > 0.55
    text = palette[2] if len(palette) > 2 else ("#1A1410" if light_fill else "#F4F8FF")
    dim = text
    shade = "#000000" if not light_fill else "#FFFFFF"
    return _Colours(fill=fill, accent=accent, text=text, dim=dim, shade=shade)


# --- layout -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    text: str
    size: int
    align: str  # "l", "c", or "lr" (label left, value right)
    role: str  # "accent" or "text"
    value: str = ""
    glow: bool = False


def _split_value(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    label, value = line.rsplit(":", 1)
    if not label.strip() or not value.strip():
        return None
    return label.strip(), value.strip()


def _layout(form: str, lines: Sequence[str], scale: float) -> list[_Line]:
    def size(name: str) -> int:
        return max(8, round(_SIZES[name] * scale))

    head, rest = lines[0], list(lines[1:])
    out: list[_Line] = []
    if form in ("level_up", "realm_breakthrough", "rank_change"):
        out.append(_Line(head, size("hero"), "c", "accent", glow=True))
        out += [_Line(line, size("body"), "c", "text") for line in rest]
    elif form == "skill_unlock":
        out.append(_Line(head, size("small"), "c", "accent"))
        if rest:
            out.append(_Line(rest[0], size("skill"), "c", "text", glow=True))
        out += [_Line(line, size("small"), "c", "text") for line in rest[1:]]
    elif form == "alert":
        out += [_Line(line, size("body"), "l", "accent" if i == 0 else "text") for i, line in enumerate(lines)]
    elif form == "dungeon_gate":
        out.append(_Line(head, size("header"), "c", "accent", glow=True))
        out += [_Line(line, size("body"), "c", "text") for line in rest]
    elif form == "system_message":
        out += [_Line(line, size("body"), "c", "text") for line in lines]
    elif form == "quest_log":
        out.append(_Line(head, size("header"), "l", "accent"))
        out += [_Line(f"• {line}", size("body"), "l", "text") for line in rest]
    elif form == "stat_sheet":
        out.append(_Line(head, size("header"), "l", "accent"))
        for line in rest:
            split = _split_value(line)
            if split is None:
                out.append(_Line(line, size("body"), "l", "text"))
            else:
                out.append(_Line(split[0], size("body"), "lr", "text", value=split[1]))
    else:  # status_window
        out.append(_Line(head, size("header"), "c", "accent"))
        for line in rest:
            split = _split_value(line)
            if split is None:
                out.append(_Line(line, size("body"), "l", "text"))
            else:
                out.append(_Line(split[0], size("body"), "lr", "text", value=split[1]))
    return out


#: The serif face sets wider than the house metric it is measured with.
SERIF_WIDTH = 1.16


def _line_width(line: _Line, *, serif: bool = False) -> float:
    width = text_width(line.text, line.size)
    if line.align == "lr":
        width += text_width(line.value, line.size) + line.size
    return width * (SERIF_WIDTH if serif else 1.0)


@dataclass(frozen=True)
class PanelBox:
    """Where one panel's box landed, in pixels (for tests and plan notes).

    Parameters
    ----------
    x, y, width, height
        The box.
    """

    x: int
    y: int
    width: int
    height: int


def _fit(
    form: str, lines: Sequence[str], *, max_width: int, frame_height: int, serif: bool = False
) -> tuple[list[_Line], int, int, int]:
    """Lay the lines out and size the box; shrink the type until the widest line fits."""

    base = frame_height / HOUSE_CANVAS_HEIGHT
    pad = max(6, round(_PAD * base))
    scale = base
    laid = _layout(form, lines, scale)
    widest = max(_line_width(line, serif=serif) for line in laid)
    room = max_width - 2 * pad - (round(14 * base) if form == "alert" else 0)
    if widest > room:
        scale = max(base * _MIN_SCALE, base * room / widest)
        laid = _layout(form, lines, scale)
        widest = max(_line_width(line, serif=serif) for line in laid)
    gap_total = sum(round(line.size * _LINE_GAP) for line in laid[1:])
    rule = round(18 * base) if form in ("status_window", "stat_sheet", "quest_log") else 0
    inner_h = sum(line.size for line in laid) + gap_total + rule
    box_w = min(max_width, round(widest) + 2 * pad + (round(14 * base) if form == "alert" else 0))
    box_w = max(box_w, round(0.36 * max_width)) if form != "alert" else box_w
    return laid, box_w, inner_h + 2 * pad, pad


def place_panel_box(
    panel: PlacedPanel,
    *,
    width: int,
    height: int,
    area: PanelArea,
) -> PanelBox:
    """Return where a panel's box sits in ``area``.

    Parameters
    ----------
    panel
        The panel.
    width, height
        The frame in pixels.
    area
        From :func:`panel_area`.

    Returns
    -------
    PanelBox
        The box, inside ``area``.
    """

    narrow = panel.position in ("upper_left", "upper_right")
    max_width = round(area.width * (NARROW_SHARE if narrow else 1.0))
    serif = panel.style.frame in SERIF_FRAMES
    _, box_w, box_h, _ = _fit(panel.form, panel.lines, max_width=max_width, frame_height=height, serif=serif)
    box_h = min(box_h, area.height)
    if panel.position == "upper_left":
        x = area.left
    elif panel.position == "upper_right":
        x = area.right - box_w
    else:
        x = area.left + (area.width - box_w) // 2
    if panel.position == "lower":
        y = area.bottom - box_h
    elif panel.position == "center":
        y = area.top + (area.height - box_h) // 2
    else:
        y = area.top
    y = max(area.top, min(y, area.bottom - box_h))
    return PanelBox(x=x, y=y, width=box_w, height=box_h)


# --- drawing ------------------------------------------------------------------------------------


def _ass_time(ms: int) -> str:
    centis = max(0, int(round(ms / 10)))
    hours, rem = divmod(centis, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return without_long_dashes(text).replace("\\", "/").replace("{", "(").replace("}", ")")


def _rounded_rect(w: int, h: int, r: int) -> str:
    r = max(0, min(r, w // 2, h // 2))
    if r == 0:
        return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"
    k = round(r * 0.45)
    return (
        f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
        f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0"
    )


def _rect(x: int, y: int, w: int, h: int) -> str:
    return f"m {x} {y} l {x + w} {y} l {x + w} {y + h} l {x} {y + h}"


def _chamfer(w: int, h: int, c: int) -> str:
    return f"m {c} 0 l {w - c} 0 l {w} {c} l {w} {h - c} l {w - c} {h} l {c} {h} l 0 {h - c} l 0 {c}"


def _corner_brackets(w: int, h: int, arm: int, t: int) -> str:
    parts = [
        _rect(0, 0, arm, t),
        _rect(0, 0, t, arm),
        _rect(w - arm, 0, arm, t),
        _rect(w - t, 0, t, arm),
        _rect(0, h - t, arm, t),
        _rect(0, h - arm, t, arm),
        _rect(w - arm, h - t, arm, t),
        _rect(w - t, h - arm, t, arm),
    ]
    return " ".join(parts)


def _brush_stroke(w: int, h: int, seed: str) -> str:
    rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16))
    steps = 14
    top = [(round(w * i / steps), round(rng.uniform(0, 0.12) * h)) for i in range(steps + 1)]
    bottom = [(round(w * i / steps), round(h - rng.uniform(0, 0.12) * h)) for i in range(steps, -1, -1)]
    lead = round(rng.uniform(0.02, 0.06) * w)
    points = [(-lead, round(h * 0.5)), *top, (w + lead, round(h * 0.45)), *bottom]
    head = f"m {points[0][0]} {points[0][1]}"
    return head + " " + " ".join(f"l {x} {y}" for x, y in points[1:])


def _box_events(
    panel: PlacedPanel, box: PanelBox, colours: _Colours, *, start: str, end: str, unit: float
) -> list[str]:
    frame = panel.style.frame
    w, h = box.width, box.height
    cx, cy = box.x + w // 2, box.y + h // 2
    border = max(1, round(_BORDER * unit))
    head = f"\\an7\\pos({box.x},{box.y})\\org({cx},{cy})\\fad({FADE_IN_MS},{FADE_OUT_MS})\\fscy30\\t(0,{OPEN_MS},\\fscy100)"
    events: list[str] = []

    def draw(layer: int, tags: str, shape: str, *, at: tuple[int, int] | None = None) -> None:
        placed = head if at is None else head.replace(f"\\pos({box.x},{box.y})", f"\\pos({at[0]},{at[1]})")
        events.append(f"Dialogue: {layer},{start},{end},Panel,,0,0,0,,{{{placed}{tags}\\p1}}{shape}{{\\p0}}")

    fill, accent = _ass_colour(colours.fill), _ass_colour(colours.accent)
    if frame == "glass":
        draw(
            0,
            f"\\1c{fill}\\1a&H58&\\3c{accent}\\3a&H20&\\bord{border}\\shad0\\blur0.6",
            _rounded_rect(w, h, round(_RADIUS * unit)),
        )
    elif frame == "hologram":
        draw(
            0,
            f"\\1c{accent}\\1a&HD0&\\3c{accent}\\3a&H60&\\bord{border}\\shad0\\blur{max(1, round(3 * unit))}",
            _rect(0, 0, w, h),
        )
        arm, t = round(min(w, h) * 0.22), max(2, round(5 * unit))
        draw(1, f"\\1c{accent}\\1a&H00&\\bord0\\shad0\\blur{max(1, round(2 * unit))}", _corner_brackets(w, h, arm, t))
    elif frame == "neon":
        draw(
            0,
            f"\\1c{fill}\\1a&H48&\\3c{accent}\\3a&H00&\\bord{border * 3}\\shad0\\blur{max(2, round(10 * unit))}",
            _rect(0, 0, w, h),
        )
        draw(1, f"\\1a&HFF&\\3c{accent}\\3a&H00&\\bord{border}\\shad0", _rect(0, 0, w, h))
    elif frame == "ink_scroll":
        rod_h = max(4, round(14 * unit))
        over = max(4, round(16 * unit))
        rod = _rounded_rect(w + 2 * over, rod_h, rod_h // 2)
        rod_tags = f"\\1c{accent}\\1a&H00&\\bord0\\shad{max(1, round(2 * unit))}\\4a&H80&"
        draw(0, f"\\1c{fill}\\1a&H14&\\bord0\\shad0", _rect(0, rod_h // 2, w, h - rod_h))
        draw(1, rod_tags, rod, at=(box.x - over, box.y))
        draw(1, rod_tags, rod, at=(box.x - over, box.y + h - rod_h))
    elif frame == "parchment":
        draw(
            0,
            f"\\1c{fill}\\1a&H18&\\3c{accent}\\3a&H00&\\bord{border}\\shad{max(1, round(3 * unit))}\\4a&H90&",
            _chamfer(w, h, round(22 * unit)),
        )
    else:  # brush
        draw(
            0, f"\\1c{fill}\\1a&H40&\\bord0\\shad0\\blur{max(1, round(2 * unit))}", _brush_stroke(w, h, panel.panel_id)
        )
    if panel.form == "dungeon_gate":
        inset = max(4, round(9 * unit))
        draw(2, f"\\1a&HFF&\\3c{accent}\\3a&H00&\\bord{max(1, border - 1)}\\shad0",
             _rect(inset, inset, w - 2 * inset, h - 2 * inset))  # fmt: skip
    if panel.form == "alert":
        bar = max(3, round(10 * unit))
        draw(2, f"\\1c{accent}\\1a&H00&\\bord0\\shad0", _rect(0, 0, bar, h))
    return events


def _typed(text: str, *, char_cs: int, delay_cs: int, face: str) -> str:
    """Karaoke typewriter: each character is hidden until its turn (``\\ko`` with a clear secondary)."""

    out = [f"{{\\ko{delay_cs}}}"] if delay_cs > 0 else []
    in_digits = False
    for char in text:
        digit = char.isdigit()
        switch = ""
        if face != FONT_NAME and digit != in_digits:
            switch = f"\\fn{FONT_NAME}" if digit else f"\\fn{face}"
            in_digits = digit
        out.append(f"{{\\ko{char_cs}{switch}}}{char}")
    return "".join(out)


def system_panel_events(
    panel: PlacedPanel,
    *,
    start_ms: int,
    end_ms: int,
    width: int,
    height: int,
    area: PanelArea,
) -> list[str]:
    """Return the ASS ``Dialogue`` lines that draw one panel from ``start_ms`` to ``end_ms``.

    Parameters
    ----------
    panel
        The panel (its form, lines and resolved style).
    start_ms, end_ms
        When it shows, on the document's timeline.
    width, height
        The play resolution.
    area
        Where panels may sit (:func:`panel_area`).

    Returns
    -------
    list[str]
        The box's events (layer 0-2) then one event per line (layer 3), typing in.
    """

    unit = height / HOUSE_CANVAS_HEIGHT
    box = place_panel_box(panel, width=width, height=height, area=area)
    narrow = panel.position in ("upper_left", "upper_right")
    max_width = round(area.width * (NARROW_SHARE if narrow else 1.0))
    serif = panel.style.frame in SERIF_FRAMES
    laid, _, _, pad = _fit(panel.form, panel.lines, max_width=max_width, frame_height=height, serif=serif)
    colours = _colours(panel.style.palette)
    start, end = _ass_time(start_ms), _ass_time(end_ms)
    events = _box_events(panel, box, colours, start=start, end=end, unit=unit)

    face = SERIF_FONT_NAME if panel.style.frame in SERIF_FRAMES else FONT_NAME
    italic = 1 if panel.style.frame == "brush" else 0
    spacing = round(2 * unit) if panel.style.frame in ("hologram", "neon") else 0
    total_chars = sum(len(line.text) + len(line.value) for line in laid) or 1
    budget_ms = max(200, int((end_ms - start_ms) * TYPE_SHARE) - OPEN_MS)
    char_cs = max(1, min(MAX_CHAR_MS, budget_ms // total_chars) // 10)
    outline = max(1, round(2 * unit))
    left_pad = pad + (round(14 * unit) if panel.form == "alert" else 0)

    y = box.y + pad
    typed_cs = OPEN_MS // 10
    for index, line in enumerate(laid):
        colour = colours.accent if line.role == "accent" else colours.text
        glow = (
            f"\\3c{_ass_colour(colours.accent)}\\3a&H40&\\bord{outline + 1}\\blur{max(1, round(4 * unit))}"
            if line.glow or panel.style.frame in ("hologram", "neon")
            else f"\\3c{_ass_colour(colours.shade)}\\3a&H70&\\bord{outline}\\blur0.8"
        )
        if panel.style.frame in ("ink_scroll", "parchment"):
            glow = "\\bord0\\blur0"
        base = (
            f"\\fn{face}\\fs{line.size}\\b1\\i{italic}\\fsp{spacing}\\1c{_ass_colour(colour)}\\2a&HFF&"
            f"\\shad0{glow}\\fad(0,{FADE_OUT_MS})"
        )
        if line.align == "c":
            anchor = f"\\an8\\pos({box.x + box.width // 2},{y})"
        else:
            anchor = f"\\an7\\pos({box.x + left_pad},{y})"
        text = _clean(line.text)
        events.append(
            f"Dialogue: 3,{start},{end},Panel,,0,0,0,,{{{anchor}{base}}}"
            + _typed(text, char_cs=char_cs, delay_cs=typed_cs, face=face)
        )
        typed_cs += char_cs * len(text)
        if line.align == "lr":
            value = _clean(line.value)
            events.append(
                f"Dialogue: 3,{start},{end},Panel,,0,0,0,,"
                f"{{\\an9\\pos({box.x + box.width - pad},{y}){base}\\1c{_ass_colour(colours.accent)}}}"
                + _typed(value, char_cs=char_cs, delay_cs=typed_cs, face=face)
            )
            typed_cs += char_cs * len(value)
        y += line.size + (round(line.size * _LINE_GAP) if index + 1 < len(laid) else 0)
        if index == 0 and panel.form in ("status_window", "stat_sheet", "quest_log"):
            rule_y = y + round(4 * unit) - round(line.size * _LINE_GAP) // 2
            events.append(
                f"Dialogue: 2,{start},{end},Panel,,0,0,0,,"
                f"{{\\an7\\pos({box.x + pad},{rule_y})\\fad({FADE_IN_MS},{FADE_OUT_MS})"
                f"\\1c{_ass_colour(colours.accent)}\\1a&H50&\\bord0\\shad0\\p1}}"
                f"{_rect(0, 0, box.width - 2 * pad, max(1, round(2 * unit)))}{{\\p0}}"
            )
            y += round(18 * unit)
    return events


def ass_header(width: int, height: int) -> str:
    """Return an ASS header with the panel style, for a ``width`` x ``height`` play resolution.

    Parameters
    ----------
    width, height
        Play resolution.

    Returns
    -------
    str
        ``[Script Info]``, ``[V4+ Styles]`` (one ``Panel`` style) and the ``[Events]`` format line.
    """

    return (
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
        f"Style: Panel,{FONT_NAME},40,&H00FFFFFF,&HFF000000,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def system_panels_ass(
    panels: Sequence[tuple[int, int, PlacedPanel]],
    *,
    width: int,
    height: int,
    picture: tuple[int, int, int, int] | None = None,
    captions_on_picture: bool = True,
) -> str | None:
    """Return the ASS document that draws ``panels`` on a ``width`` x ``height`` frame.

    Parameters
    ----------
    panels
        ``(start_ms, end_ms, panel)`` on the document's timeline.
    width, height
        Play resolution.
    picture
        The picture's box when it does not fill the frame (:func:`panel_area`).
    captions_on_picture
        Whether burned captions sit on the picture.

    Returns
    -------
    str | None
        The document, or ``None`` when there is no panel to draw.
    """

    if not panels:
        return None
    area = panel_area(width, height, picture=picture, captions_on_picture=captions_on_picture)
    events: list[str] = []
    for start_ms, end_ms, panel in panels:
        if end_ms <= start_ms:
            continue
        events += system_panel_events(panel, start_ms=start_ms, end_ms=end_ms, width=width, height=height, area=area)
    if not events:
        return None
    return ass_header(width, height) + "".join(f"{event}\n" for event in events)




def burn_panels(
    panels: Sequence[tuple[int, int, PlacedPanel]],
    video: Path,
    ass_path: Path,
    out: Path,
    *,
    picture: tuple[int, int, int, int] | None = None,
) -> Path:
    """Write the panels' ASS to ``ass_path`` and burn it onto ``video`` as ``out`` (house fonts, libass).

    Parameters
    ----------
    panels
        ``(start_ms, end_ms, panel)`` on the take's timeline.
    video, ass_path, out
        The take, the ASS to write, the burned take.
    picture
        The picture's box on a letterbox canvas (``x, y, w, h``); ``None`` when it fills the frame.

    Returns
    -------
    Path
        ``out``.
    """

    from creation.captions import burn_ass, find_ffmpeg
    from creation.post.media import probe_video

    info = probe_video(video)
    document = system_panels_ass(
        panels, width=info.width, height=info.height, picture=picture, captions_on_picture=picture is None
    )
    if document is None:
        raise ValueError("no system panel to burn")
    ass_path.write_text(document, encoding="utf-8")
    burn_ass(find_ffmpeg()[0], video, ass_path, out)
    return out


#: A panel cut short by the reel is kept when this much of it plays (seconds) ...
PANEL_MIN_VISIBLE_SECONDS = 1.0
#: ... or this share of it does.
PANEL_VISIBLE_FRACTION = 0.8
_TAKE_NUMBER = re.compile(r"^t(\d+)$")


def reel_panels(
    spine: Mapping[str, Any] | None,
    episode_id: str,
    takes: Sequence[tuple[str, float]],
    segments: Sequence[Any],
    *,
    fps: float = 24.0,
    skip_takes: Sequence[str] = (),
) -> list[tuple[int, int, PlacedPanel]]:
    """Return the episode's panels moved onto a reel through its cut (fictora-drama ``reel_system_panels``).

    Parameters
    ----------
    spine
        The spine as JSON.
    episode_id
        The episode.
    takes
        ``(take_id, duration_s)`` of the reel's takes (``t1``, ``t2`` ...).
    segments
        The reel's segments (``creation.post.reel_plan.Segment``) in order.
    fps
        The reel's frame rate.
    skip_takes
        Takes cut from a file whose text is already burned in (it carries its panels).

    Returns
    -------
    list[tuple[int, int, PlacedPanel]]
        ``(start_ms, end_ms, panel)`` on the reel's timeline.
    """

    from creation.post.reel_plan import contiguous_runs, segment_map

    numbered = {take_id: int(m.group(1)) for take_id, _ in takes if (m := _TAKE_NUMBER.match(take_id))}
    if not numbered:
        return []
    last = max(numbered.values())
    lengths = {numbered[t]: d for t, d in takes if t in numbered}
    durations = [round(lengths[n] * 1000) if lengths.get(n) else NOMINAL_TAKE_MS for n in range(1, last + 1)]
    placed = place_panels(spine, episode_id, durations)
    starts = [sum(durations[:index]) / 1000.0 for index in range(len(durations))]
    out: list[tuple[int, int, PlacedPanel]] = []
    for seg, at in segment_map(contiguous_runs(list(segments), fps), fps):
        if seg.take in skip_takes or seg.take not in numbered:
            continue
        a, b = round(seg.start * fps) / fps, round(seg.end * fps) / fps
        for panel in placed:
            if panel.take != numbered[seg.take]:
                continue
            p_start = panel.start_ms / 1000.0 - starts[panel.take - 1]
            p_end = panel.end_ms / 1000.0 - starts[panel.take - 1]
            start, end = max(p_start, a), min(p_end, b)
            seen = end - start
            if seen <= 0:
                continue
            if seen < PANEL_MIN_VISIBLE_SECONDS and seen < PANEL_VISIBLE_FRACTION * (p_end - p_start):
                continue
            out.append((round((at + start - a) * 1000), round((at + end - a) * 1000), panel))
    return sorted(out, key=lambda item: item[0])


__all__ = [
    "DEFAULT_LOOK",
    "SYSTEM_PANEL_GENRES",
    "PanelStyle",
    "PlacedPanel",
    "burn_panels",
    "panel_area",
    "place_panel_box",
    "place_panels",
    "reel_panels",
    "show_takes_panels",
    "system_panel_events",
    "system_panels_ass",
    "take_panels",
    "without_long_dashes",
]
