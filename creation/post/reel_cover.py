"""Reel cover image and the reel results sheet ($0, local, no server call).

Founder decisions of 6 Oct 2026. Instagram ignores a cover attached inside an
MP4 (``attached_pic``): a reel's cover is picked in the Instagram editor
("Edit cover", then "Add from camera roll"), so every ``reel`` writes a
separate still next to the reel, ``reels/<reel stem>-cover-vN.jpg`` (never
overwritten), and the post text tells the operator to upload it. Since 7 Oct
2026 the same image is also the reel's first frame (:mod:`creation.post.cover_frame`):
Discord and the phones show a video's first frame as its preview.

* **Picture.** ``--cover-frame S`` (seconds on the reel) when given; else a
  cover the server already drew for one of the episode's takes
  (:func:`creation.post.thumbnail.saved_cover`, the cold open's take first);
  else the reel plan's strongest frame (:mod:`creation.post.reel_plan`'s
  scorer; it never comes from the new fact, so the cover never spoils the
  ending). Grabbed from the take's picture before captions, so no caption or
  mark is under the text.
* **Text** (founder decision, 6 Oct 2026: the show's name sells it, the part
  number follows). The series title large in the house yellow, on at most two
  lines (a long title wraps, then shrinks to fit), and "PART N" under it in
  white at about half the title's size, both in the house font
  (:data:`creation.captions.FONT_NAME`) with a heavy black outline and shadow
  like the captions. Sized to read on the profile grid, where a cover is about
  a third of the phone's width. The block stays out of the
  covered zones (top 8%, bottom 20%, right 12% of the lower two thirds,
  :data:`creation.post.safe_zones.ZONES`) and inside the middle 3:4 that the
  profile grid shows. It sits just above the bottom band, or just under the
  top strip when a detected face is there (faces are read on the chosen
  picture when a detector runs; with none it stays low, where a portrait
  face rarely is).

"PART N" is never on screen in the reel itself (the reel's viewer-address
rule cuts it); it lives only on the cover and in the post text.

Every rendered reel also puts its row in ``reels/metrics.csv`` (made with its
header when missing), one row per part, updated to the newest reel
(:func:`record_metrics_row`): what was posted and how, plus blank columns the
operator fills from Instagram's insights (:data:`METRICS_COLUMNS`).
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.caption_dashes import drawn_text as no_dash_text
from creation.captions import FONT_NAME, FONTS_DIR, _ass_escape, text_width
from creation.ops.folder import next_versioned_path
from creation.post.hook_overlay import (
    BAND_GAP,
    RIGHT_RAIL_X,
    SIDE_MARGIN,
    TOP_STRIP,
    _fit,
    _scaled,
)

#: The series title on the 1920-high canvas (the house caption is 64): at most two lines, shrinking
#: to fit the safe width when a long title needs it.
TITLE_FONT_SIZE = 150
#: "PART N": about half the title's size (half its cap height), never under this on 1920.
PART_SCALE = 0.5
PART_MIN_FONT_SIZE = 60
COVER_OUTLINE = 9
COVER_SHADOW = 4
#: Gap between the title and "PART N", as a share of PART N's size.
PART_GAP = 0.3
#: The bottom band the platforms cover (post caption, username, music) starts here.
BOTTOM_BAND = 0.80
#: Instagram's profile grid shows the middle 3:4 of a 9:16 cover: keep the text inside it.
GRID_TOP = 0.125
GRID_BOTTOM = 0.875
#: House yellow (``#FFE500``) for the title and white for PART N, as ASS ``&HAABBGGRR``.
TITLE_COLOUR = "&H0000E5FF"
PART_COLOUR = "&H00FFFFFF"
#: The reel results sheet's columns, in order: one row per part (``superseded`` lists the older reels
#: it replaced); the last eight are the operator's, from Instagram.
METRICS_COLUMNS: tuple[str, ...] = (
    "reel_file", "cover_file", "series", "part", "account", "lane", "planned_post_slot",
    "cold_open_role", "cold_open_time", "hook_text", "superseded",
    "posted_at", "views", "hold_3s_pct", "avg_watch_pct", "follows", "saves", "shares", "notes",
)  # fmt: skip
METRICS_FILE = "metrics.csv"

#: A box as frame fractions ``(left, top, right, bottom)`` (:mod:`creation.post.safe_zones`).
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class CoverLayout:
    """Where the cover's text goes and how big it is, in pixels of the picture.

    Parameters
    ----------
    placement
        ``lower`` (just above the bottom band) or ``upper`` (just under the top strip).
    part_text, part_size
        "PART N" and its font size (about half the title's).
    title_lines, title_size
        The series title's lines (at most two) and font size.
    centre_x, top_px, title_top_px, part_top_px
        The block's centre, its top, and the top of the title and of PART N, in pixels.
    box
        The text block as frame fractions ``(left, top, right, bottom)``.
    face_overlap
        True when every place overlaps a detected face (the one that overlaps least is used).
    """

    placement: str
    part_text: str
    part_size: int
    title_lines: tuple[str, ...]
    title_size: int
    centre_x: int
    top_px: int
    title_top_px: int
    part_top_px: int
    box: Box
    width: int
    height: int
    face_overlap: bool = False


#: A desk's name naming its one episode: ``noclip-ep03``, ``2026-10-08-show-episode-4``, ``show_part_2``.
_DESK_PART = re.compile(
    r"(?:^|[^a-z0-9])(?:ep|episode|part)[-_ ]?0*(\d{1,3})(?:$|[^0-9])"
)


@dataclass(frozen=True)
class SeriesPart:
    """The number "PART N" shows for one episode of a desk, and where it came from."""

    number: int
    #: ``--part``, ``desk setting``, ``desk name`` or ``desk ordinal``.
    source: str

    @property
    def from_desk_ordinal(self) -> bool:
        """True when nothing said otherwise: the desk's own episode ordinal."""

        return self.source == "desk ordinal"

    def note(self) -> str:
        """One line for the reel's report."""

        if self.from_desk_ordinal:
            return f"PART {self.number} (the desk's episode ordinal; reel --part N sets the series number)"
        return f"PART {self.number} (from the {self.source}; reel --part N changes it)"


def series_part(desk: Path, episode: int, override: int | None = None) -> SeriesPart:
    """The series episode number of ``episode`` on ``desk``, for "PART N" (NOCLIP, L-20261008-9).

    A show made one desk per episode has episode 1 on every desk, so the
    desk's ordinal is the wrong number. First that answers:

    1. ``override`` (``reel --part N``);
    2. the desk's ``first_part`` setting (``production.config.json``, saved by
       ``reel --part``): ``first_part + episode - 1``;
    3. a one-episode desk whose folder name says its number (``noclip-ep03``);
    4. the desk's own episode ordinal (every multi-episode desk, as before).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal on the desk.
    override
        ``--part N``.

    Returns
    -------
    SeriesPart
        The number and its source.

    Raises
    ------
    ValueError
        When ``override`` is under 1.
    """

    if override is not None:
        if override < 1:
            raise ValueError(f"--part must be 1 or more, not {override}")
        return SeriesPart(override, "--part")
    from creation.production_config import load_production_config

    try:
        first = load_production_config(desk).first_part
    except (OSError, ValueError, TypeError):
        first = None
    if isinstance(first, int) and first >= 1:
        return SeriesPart(first + episode - 1, "desk setting")
    if episode == 1:
        try:
            from creation.ops.state import load_series

            slots = len(load_series(desk).episodes)
        except (OSError, ValueError, KeyError, TypeError):
            slots = 0
        found = _DESK_PART.search(desk.expanduser().resolve().name.lower())
        if slots == 1 and found and int(found.group(1)) >= 1:
            return SeriesPart(int(found.group(1)), "desk name")
    return SeriesPart(episode, "desk ordinal")


def remember_part(desk: Path, episode: int, part: int) -> Path:
    """Save ``reel --part N`` on the desk so ``finish``, ``join`` and later reels keep it.

    Stored as ``first_part`` (the series number of the desk's episode 1) in
    ``production.config.json``.

    Raises
    ------
    ValueError
        When ``part`` is lower than ``episode`` would allow (``first_part`` under 1).
    """

    from creation.production_config import (
        load_production_config,
        save_production_config,
    )

    first = part - episode + 1
    if first < 1:
        raise ValueError(
            f"--part {part} on the desk's episode {episode} would put its episode 1 before part 1"
        )
    config = load_production_config(desk)
    config.first_part = first
    return save_production_config(desk, config)


def _overlap(box: Box, face: tuple[float, float, float, float]) -> float:
    """Area shared by ``box`` (left, top, right, bottom) and ``face`` (x, y, w, h), as frame fractions."""

    left, top, right, bottom = box
    x, y, w, h = face
    return max(0.0, min(right, x + w) - max(left, x)) * max(
        0.0, min(bottom, y + h) - max(top, y)
    )


def cover_layout(
    *,
    series: str,
    part: int,
    width: int,
    height: int,
    faces: Sequence[tuple[float, float, float, float]] = (),
    picture_rows: tuple[int, int] | None = None,
) -> CoverLayout:
    """Lay out "PART N" and the series title on a ``width`` x ``height`` picture.

    Parameters
    ----------
    series
        The series title (empty: only "PART N").
    part
        The number "PART N" shows: the series episode number (:func:`series_part`).
    width, height
        The picture's size in pixels.
    faces
        Detected face boxes as frame fractions ``(x, y, w, h)``; empty when unknown.
    picture_rows
        A letterbox cover only: the rows ``(top, bottom)`` the 4:3 picture fills on the
        9:16 canvas. The text block then stays inside them, clear of the title band
        above and the caption band below (NOCLIP, L-20261008-9), shrinking if it must.
        ``None`` (every portrait cover): the portrait zones, exactly as before.

    Returns
    -------
    CoverLayout
        Lower by default; upper when a face sits where the lower block would go
        and not where the upper one would.
    """

    left = round(SIDE_MARGIN * width / 1080)
    right = width - round(RIGHT_RAIL_X * width) + left
    room = max(1, width - left - right)
    centre_x = left + room // 2
    part_text = f"PART {part}"
    # No em or en dash on the picture (6 Oct 2026; creation.caption_dashes).
    title = no_dash_text(" ".join(series.split()))
    title_lines: list[str] = []
    title_size = _scaled(TITLE_FONT_SIZE, height)
    if title:
        # Two balanced lines at most; a title still too wide shrinks until it fits.
        title_lines, title_size = _fit(title, title_size, room, 8)
    part_size = min(
        max(round(PART_SCALE * title_size), _scaled(PART_MIN_FONT_SIZE, height)),
        title_size if title_lines else _scaled(TITLE_FONT_SIZE, height),
    )
    if text_width(part_text, part_size) > room:
        part_size = max(8, int(part_size * room / text_width(part_text, part_size)))
    title_h = title_size * len(title_lines)
    gap = round(PART_GAP * part_size) if title_lines else 0
    block_h = title_h + gap + part_size
    margin = round(BAND_GAP * height)
    if picture_rows is not None:
        room_h = max(1, picture_rows[1] - picture_rows[0] - 2 * margin)
        if block_h > room_h:
            # A tall title on a short 4:3 picture: shrink the whole block to fit between the bands.
            scale = room_h / block_h
            title_size = max(8, int(title_size * scale))
            part_size = max(8, int(part_size * scale))
            title_h = title_size * len(title_lines)
            gap = round(PART_GAP * part_size) if title_lines else 0
            block_h = title_h + gap + part_size
    widest = max(
        [text_width(part_text, part_size)]
        + [text_width(line, title_size) for line in title_lines]
    )
    half = min(widest, room) / 2

    def placed(name: str) -> tuple[int, Box]:
        if picture_rows is not None:
            # Letterbox: inside the picture, never on the title band or the caption band.
            top_px = (
                picture_rows[0] + margin
                if name == "upper"
                else picture_rows[1] - margin - block_h
            )
        elif name == "upper":
            top_px = round((max(TOP_STRIP, GRID_TOP) + BAND_GAP) * height)
        else:
            bottom = (min(BOTTOM_BAND, GRID_BOTTOM) - BAND_GAP) * height
            top_px = round(bottom - block_h)
        box = (
            (centre_x - half) / width,
            top_px / height,
            (centre_x + half) / width,
            (top_px + block_h) / height,
        )
        return top_px, box

    options = {name: placed(name) for name in ("lower", "upper")}
    cost = {
        name: sum(_overlap(box, face) for face in faces)
        for name, (_, box) in options.items()
    }
    chosen = min(("lower", "upper"), key=lambda name: (cost[name] > 0, cost[name]))
    top_px, box = options[chosen]
    return CoverLayout(
        placement=chosen, part_text=part_text, part_size=part_size,
        title_lines=tuple(title_lines), title_size=title_size, centre_x=centre_x, top_px=top_px,
        title_top_px=top_px, part_top_px=top_px + title_h + gap, box=box, width=width, height=height,
        face_overlap=all(c > 0 for c in cost.values()),
    )  # fmt: skip


def cover_ass(layout: CoverLayout) -> str:
    """The ASS that draws the layout's text on its picture (house font, outline and shadow)."""

    outline = _scaled(COVER_OUTLINE, layout.height)
    shadow = _scaled(COVER_SHADOW, layout.height)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {layout.width}\n"
        f"PlayResY: {layout.height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Title,{FONT_NAME},{layout.title_size},{TITLE_COLOUR},{TITLE_COLOUR},&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},8,0,0,0,1\n"
        f"Style: Part,{FONT_NAME},{layout.part_size},{PART_COLOUR},{PART_COLOUR},&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(2, outline * 2 // 3)},{shadow},8,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    if layout.title_lines:
        events.append(
            f"Dialogue: 0,0:00:00.00,9:59:59.00,Title,,0,0,0,,"
            f"{{\\an8\\pos({layout.centre_x},{layout.title_top_px})}}"
            + "\\N".join(_ass_escape(line) for line in layout.title_lines)
            + "\n"
        )
    events.append(
        f"Dialogue: 0,0:00:00.00,9:59:59.00,Part,,0,0,0,,"
        f"{{\\an8\\pos({layout.centre_x},{layout.part_top_px})}}{_ass_escape(layout.part_text)}\n"
    )
    return header + "".join(events)


def cover_path(reel: Path) -> Path:
    """The next free ``<reel stem>-cover-vN.jpg`` beside ``reel`` (never an existing file)."""

    return next_versioned_path(reel.parent, f"{reel.stem}-cover", ".jpg")


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def draw_cover(
    picture: Path, out: Path, *, layout: CoverLayout, at: float | None, scratch: Path
) -> Path:
    """Grab one frame (or read a still) and burn the layout's text on it as a JPEG.

    Parameters
    ----------
    picture
        A video (with ``at``) or a still image (``at`` ``None``).
    out
        The cover file (must not exist yet).
    layout
        From :func:`cover_layout`, at the picture's size.
    at
        Seconds into ``picture`` when it is a video.
    scratch
        A folder for the ASS file (never the desk).

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    FileExistsError
        When ``out`` exists.
    """

    from creation.post.media import run_ffmpeg

    if out.exists():
        raise FileExistsError(f"{out} exists; the cover never overwrites")
    ass = scratch / "cover.ass"
    ass.write_text(cover_ass(layout), encoding="utf-8")
    seek = ["-ss", f"{max(0.0, at):.3f}"] if at is not None else []
    run_ffmpeg(
        [*seek, "-i", str(picture), "-frames:v", "1",
         "-vf", f"ass='{_filter_path(ass)}':fontsdir='{_filter_path(FONTS_DIR)}'",
         "-q:v", "2", str(out)]
    )  # fmt: skip
    return out


#: Widths the cover's picture is read at for faces (aspect kept): a take's reading width and twice it.
#: The cascades are brittle on drawn faces; two scales find more of them than one.
COVER_FACE_WIDTHS = (270, 540)


def face_boxes(
    picture: Path, at: float | None, detector: Any, *, scratch: Path | None = None
) -> list[tuple[float, float, float, float]] | None:
    """Face boxes on the cover's picture as frame fractions ``(x, y, w, h)``; ``None`` when no detector runs.

    The picture is read as a still at its own aspect (a video's frame at ``at``
    is grabbed first, into ``scratch``), at each of :data:`COVER_FACE_WIDTHS`;
    the boxes of every width are kept.

    Parameters
    ----------
    picture
        A still image, or a video with ``at``.
    at
        Seconds into the video; ``None`` for a still.
    detector
        From :func:`creation.post.faces.local_detector` (``None``: not read).
    scratch
        A folder for the grabbed frame (a temporary one when not given).

    Returns
    -------
    list | None
        The boxes (empty: none found), or ``None`` when no detector ran.
    """

    if detector is None:
        return None
    import tempfile

    import numpy as np
    from PIL import Image

    from creation.post.faces import upper_band_face
    from creation.post.media import run_ffmpeg

    with tempfile.TemporaryDirectory(prefix="fictora-cover-face-") as tmp:
        still = picture
        if at is not None:
            still = (scratch or Path(tmp)) / "cover-face-frame.png"
            run_ffmpeg(
                ["-ss", f"{max(0.0, at):.3f}", "-i", str(picture), "-frames:v", "1", str(still)]
            )  # fmt: skip
        with Image.open(still) as image:
            rgb = image.convert("RGB")
        boxes: list[tuple[float, float, float, float]] = []
        for width in COVER_FACE_WIDTHS:
            height = max(1, round(rgb.height * width / rgb.width))
            frame = np.asarray(rgb.resize((width, height), Image.LANCZOS))
            found = upper_band_face(frame, top=0.0, bottom=1.0, detector=detector)
            if found is None:
                return None
            boxes += [box for _, box in found.boxes]
    return boxes


#: Printed when no face box is found on the cover's picture.
NO_FACE_NOTE = "⚠ no face found on the cover picture — look at the cover before posting"


def face_note(faces: Sequence[tuple[float, float, float, float]] | None) -> str | None:
    """The ⚠ to print when the cover's picture has no face box (``None`` when one was found).

    Parameters
    ----------
    faces
        :func:`face_boxes`' answer: boxes, an empty list (none found) or ``None`` (no detector ran).

    Returns
    -------
    str | None
        :data:`NO_FACE_NOTE` (with why when no detector ran), or ``None``.
    """

    if faces is None:
        return f"{NO_FACE_NOTE} (no face detector ran: OpenCV did not load; `uv sync`)"
    return None if faces else NO_FACE_NOTE


#: The columns the operator fills from Instagram; a row with any of them filled was posted.
#: A TikTok clip's columns (``kind`` ``reel`` / ``clip``, the clip's number), after ``part`` once a desk has clips.
CLIP_COLUMNS: tuple[str, ...] = ("kind", "clip")
OPERATOR_COLUMNS = (
    "posted_at",
    "views",
    "hold_3s_pct",
    "avg_watch_pct",
    "follows",
    "saves",
    "shares",
)


def record_metrics_row(sheet: Path, row: Mapping[str, Any]) -> Path:
    """Put one reel (or one clip) in the results sheet: one row per part (per part and clip), updated to the newest.

    The part's row that was not posted yet (no :data:`OPERATOR_COLUMNS` filled)
    is updated in place: the kit's columns take the new reel, the old reel file
    joins ``superseded`` (oldest first, ``; `` between), the operator's columns
    and ``notes`` stay. A posted row is history and never changed: a re-cut
    after posting gets a new row. The header is written when the file is new;
    a sheet from an older kit gains the new columns. A TikTok clip's row is
    ``kind`` ``clip`` with its number in ``clip`` (:data:`CLIP_COLUMNS`, after
    ``part``): its key is the part and the clip, apart from the reel's row. A
    sheet gets those two columns with its first clip (every reel row then says
    ``reel``); a sheet with no clip (an older desk's) keeps its columns.

    Parameters
    ----------
    sheet
        ``<desk>/reels/metrics.csv``.
    row
        Values by column name (:data:`METRICS_COLUMNS`); ``part`` (with ``kind`` and ``clip``) is the key, a
        missing column is blank (``kind`` blank is ``reel``).

    Returns
    -------
    Path
        ``sheet``.
    """

    rows: list[dict[str, str]] = []
    if sheet.is_file() and sheet.stat().st_size:
        with sheet.open(encoding="utf-8", newline="") as handle:
            rows = [dict(r) for r in csv.DictReader(handle)]
    new = {
        name: str(row.get(name, "") or "") for name in (*METRICS_COLUMNS, *CLIP_COLUMNS)
    }
    clipped = new["kind"] == "clip" or any(r.get("kind") for r in rows)
    if clipped:
        new["kind"] = new["kind"] or "reel"
        for r in rows:
            r["kind"] = r.get("kind") or "reel"

    def key(r: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            str(r.get("part") or ""),
            str(r.get("kind") or "reel"),
            str(r.get("clip") or ""),
        )

    open_row = next(
        (
            r
            for r in rows
            if key(r) == key(new)
            and not any((r.get(c) or "").strip() for c in OPERATOR_COLUMNS)
        ),
        None,
    )
    if open_row is None:
        rows.append(new)
    else:
        older = [x for x in (open_row.get("superseded") or "").split("; ") if x]
        if open_row.get("reel_file") and open_row["reel_file"] != new["reel_file"]:
            older.append(open_row["reel_file"])
        keep = {c: open_row.get(c, "") for c in (*OPERATOR_COLUMNS, "notes")}
        open_row.update({**new, **keep, "superseded": "; ".join(older)})
    base = list(METRICS_COLUMNS)
    if clipped:
        at = base.index("part") + 1
        base[at:at] = list(CLIP_COLUMNS)
    extra = [
        c
        for r in rows
        for c in r
        if c not in base and c and not (c in CLIP_COLUMNS and not clipped)
    ]
    columns = [*base, *dict.fromkeys(extra)]
    scratch = sheet.with_name(sheet.name + ".tmp")
    with scratch.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({c: r.get(c, "") or "" for c in columns} for r in rows)
    scratch.replace(sheet)
    return sheet


__all__ = [
    "CLIP_COLUMNS",
    "METRICS_COLUMNS",
    "METRICS_FILE",
    "CoverLayout",
    "SeriesPart",
    "record_metrics_row",
    "remember_part",
    "series_part",
    "cover_ass",
    "cover_layout",
    "cover_path",
    "draw_cover",
    "face_boxes",
    "face_note",
]
