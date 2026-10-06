"""``review``: measure the safe zones on a finished take (warn, never block).

TikTok, Reels and Shorts draw their own interface over the video: the top 8%
(tabs, search, camera), the bottom 20% (post caption, username, music) and the
right 12% of the lower two thirds (like, comment, share). The board gate reads
the written placements only; this reads the filmed pixels.

What is measured, on frames sampled across the take:

- **captions**: the house caption is yellow (``#FFE500``), so its box is found
  by colour on each frame and checked against the covered zones and against
  the house caption band (55-70% of the frame height).
- **caption layout**: when the burned caption file the kit wrote is beside the
  take (``take-epNN-tK-cap-vN.ass``), every cue's box is laid out from it
  (frame size, style font size, alignment and margins, ``\\pos``/``\\an``,
  the cue's lines at the font's measured width) and checked the same way, with
  the cue's times. That covers each cue, not only the sampled frames, and a
  white ``plain`` caption the colour search cannot see.
- **faces**: this kit carries no face detector (its dependencies are numpy
  and Pillow only), so faces are not measured. ``review`` writes a contact
  sheet of the sampled frames with the covered zones shaded, for the human to
  check that no face, eyes, mouth or key prop sits in them. The board's
  written placements are no longer flagged: only a measured box is a finding.

A letterbox show's 9:16 file (its finish record says ``letterbox``) is
checked against its own band: captions sit under the picture, between the
picture's bottom edge and the platform chrome (y 1385-1536, 72-80% on 1920), and the
yellow hook line above the picture is the title, not a caption.

Everything is a warning: ``review`` always exits 0 and changes no take.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, TextIO

import numpy as np
from PIL import Image, ImageDraw

from creation.captions import CAPTION_BAND, text_width
from creation.ops.folder import next_versioned_path
from creation.post.media import ffmpeg_bin, probe_video

#: Covered zones as ``(left, top, right, bottom)`` fractions of the frame (#432).
ZONES: dict[str, tuple[float, float, float, float]] = {
    "top strip": (0.0, 0.0, 1.0, 0.08),
    "bottom band": (0.0, 0.80, 1.0, 1.0),
    "right rail": (0.88, 1.0 / 3.0, 1.0, 1.0),
}
ZONE_WHAT = {
    "top strip": "the top 8% (tabs, search, camera)",
    "bottom band": "the bottom 20% (post caption, username, music)",
    "right rail": "the right 12% of the lower two thirds (like, comment, share)",
}
SAMPLE_FRAMES = 8
#: A row belongs to a caption when at least this share of its pixels are caption yellow.
CAPTION_ROW_SHARE = 0.01
#: A caption box narrower than this share of the frame is not a caption (a prop, a light).
CAPTION_MIN_WIDTH = 0.06
FACE_CHECK = (
    "faces: not measured (no face detector in this kit: numpy and Pillow only). Look at {sheet}: the covered "
    "zones are shaded red; check no face, eyes, mouth or key prop sits in them."
)
#: Line height of a caption line per ASS ``Fontsize`` unit: libass sets a ``Fontsize`` as the
#: face's winAscent + winDescent (:data:`creation.captions.HOUSE_EM_PER_SIZE`), one line's height.
LINE_HEIGHT = 1.0

Box = tuple[float, float, float, float]


@dataclass
class FrameCheck:
    """One sampled frame: when, its caption box (fractions) and the zones that box enters."""

    seconds: float
    caption: Box | None
    zones: list[str] = field(default_factory=list)
    outside_band: bool = False
    #: ``pixels`` (found by colour) or ``layout`` (the burned caption file's cue at this time).
    measured_by: str = "pixels"


@dataclass(frozen=True)
class LayoutCue:
    """One cue of the burned caption file: when it shows, its text and the box it is laid out in."""

    start: float
    end: float
    text: str
    box: Box

    @property
    def zones(self) -> list[str]:
        """The covered zones the cue's box enters."""

        return zones_entered(self.box)


@dataclass
class SafeZoneReport:
    """What ``review`` measured on one file."""

    source: Path
    frames: list[FrameCheck]
    sheet: Path
    warnings: list[str]
    layout: Path | None = None
    cues: list[LayoutCue] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        """JSON-ready report."""

        return {
            "source": str(self.source),
            "sheet": str(self.sheet),
            "zones": ZONES,
            "frames": [asdict(f) for f in self.frames],
            "warnings": self.warnings,
            "caption_layout": str(self.layout) if self.layout else None,
            "cues": [asdict(cue) | {"zones": cue.zones} for cue in self.cues],
            "faces_measured": False,
        }


def caption_mask(pixels: np.ndarray) -> np.ndarray:
    """Pixels in the house caption yellow (``#FFE500`` after compression), as a boolean mask."""

    r, g, b = (pixels[..., i].astype(int) for i in range(3))
    return (r >= 200) & (g >= 170) & (b <= 120) & (r - b >= 120)


def _caption_box_in_rows(mask: np.ndarray, row_lo: int, row_hi: int) -> Box | None:
    """The densest run of caption-yellow rows inside ``[row_lo, row_hi)``.

    A ceiling lamp and pale hair are yellow too. Joining every yellow row into
    one box stretches the zone from the lamp down to the real caption. One
    contiguous run is the caption.
    """

    height, width = mask.shape
    if row_hi <= row_lo:
        return None
    threshold = max(2, CAPTION_ROW_SHARE * width)
    yellow = np.flatnonzero(mask[row_lo:row_hi].sum(axis=1) >= threshold) + row_lo
    if yellow.size == 0:
        return None
    runs: list[tuple[int, int]] = []
    start = previous = int(yellow[0])
    for raw in yellow[1:]:
        row = int(raw)
        if row > previous + 1:
            runs.append((start, previous))
            start = row
        previous = row
    runs.append((start, previous))
    best_score = -1
    best: tuple[int, int] | None = None
    for run_start, run_end in runs:
        score = int(mask[run_start : run_end + 1].sum())
        if score > best_score:
            best_score = score
            best = (run_start, run_end)
    assert best is not None
    run_start, run_end = best
    cols = np.flatnonzero(mask[run_start : run_end + 1].any(axis=0))
    if cols.size == 0:
        return None
    left, right = cols.min() / width, (cols.max() + 1) / width
    if right - left < CAPTION_MIN_WIDTH:
        return None
    return (
        round(float(left), 4),
        round(run_start / height, 4),
        round(float(right), 4),
        round((run_end + 1) / height, 4),
    )


def caption_box(image: Image.Image, *, below: float | None = None) -> Box | None:
    """The caption's box as ``(left, top, right, bottom)`` fractions, or ``None`` when no caption shows.

    The house band (about 50–78% of the height) is searched first. A yellow
    lamp or highlight outside that band does not stretch the box. A caption
    that sits in the covered bottom band is still found, on the whole frame,
    so the warning still fires.
    """

    pixels = np.asarray(image.convert("RGB"))
    height = pixels.shape[0]
    mask = caption_mask(pixels)
    if below is not None:
        # A letterbox file: its captions are under the picture; the yellow above it is the title.
        return _caption_box_in_rows(mask, int(below * height), height)
    band = _caption_box_in_rows(mask, int(0.50 * height), int(0.78 * height))
    if band is not None:
        return band
    return _caption_box_in_rows(mask, 0, height)


def zones_entered(box: Box) -> list[str]:
    """The covered zones a box overlaps."""

    left, top, right, bottom = box
    return [
        name
        for name, (zl, zt, zr, zb) in ZONES.items()
        if min(right, zr) - max(left, zl) > 0 and min(bottom, zb) - max(top, zt) > 0
    ]


_POS = re.compile(r"\\pos\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)")
_AN = re.compile(r"\\an(\d)")
_FS = re.compile(r"\\fs([\d.]+)")
_TAGS = re.compile(r"\{[^}]*\}")


def _ass_seconds(stamp: str) -> float:
    hours, minutes, seconds = stamp.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def caption_layout(ass: Path, *, width: int, height: int) -> list[LayoutCue]:
    """Lay out every cue of a burned caption file and return its box on a ``width`` x ``height`` frame.

    Reads what libass places by: ``PlayResX/Y`` (scaled to the frame), each style's
    ``Fontsize``, ``Alignment`` and margins, a cue's own margins, and ``\\pos``,
    ``\\an`` and ``\\fs`` overrides; the cue's ``\\N`` lines are measured at the
    font's width (:func:`creation.captions.text_width`). An estimate to a few pixels,
    which is what a zone check needs.

    Parameters
    ----------
    ass
        The ``.ass`` the take's captions were burned from.
    width, height
        The take's frame size.

    Returns
    -------
    list[LayoutCue]
        One per ``Dialogue`` event with text, in file order.
    """

    play_x, play_y = float(width), float(height)
    fields: list[str] = []
    styles: dict[str, dict[str, str]] = {}
    events: list[list[str]] = []
    event_fields: list[str] = []
    for raw in ass.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "PlayResX":
            play_x = float(value)
        elif key == "PlayResY":
            play_y = float(value)
        elif key == "Format" and "Fontsize" in value:
            fields = [name.strip() for name in value.split(",")]
        elif key == "Format":
            event_fields = [name.strip() for name in value.split(",")]
        elif key == "Style" and fields:
            styles[value.split(",", 1)[0].strip()] = dict(
                zip(fields, (part.strip() for part in value.split(",")), strict=False)
            )
        elif key == "Dialogue" and event_fields:
            events.append(value.split(",", len(event_fields) - 1))
    sx, sy = width / play_x, height / play_y
    cues: list[LayoutCue] = []
    for parts in events:
        event = dict(zip(event_fields, parts, strict=False))
        raw_text = event.get("Text", "")
        text_lines = [_TAGS.sub("", part) for part in raw_text.split("\\N")]
        text = " ".join(part for part in text_lines if part.strip())
        if not text.strip():
            continue
        style = styles.get(event.get("Style", "").strip()) or next(
            iter(styles.values()), {}
        )
        size = float(style.get("Fontsize") or 48)
        if found := _FS.search(raw_text):
            size = float(found.group(1))
        align = int(style.get("Alignment") or 2)
        if found := _AN.search(raw_text):
            align = int(found.group(1))

        def margin(name: str) -> float:
            own = (event.get(name) or "0").strip()
            return float(own) if own not in ("", "0") else float(style.get(name) or 0)

        block_w = max(text_width(part, round(size)) for part in text_lines) * sx
        block_h = len(text_lines) * size * LINE_HEIGHT * sy
        if found := _POS.search(raw_text):
            anchor_x, anchor_y = float(found.group(1)) * sx, float(found.group(2)) * sy
        else:
            column = (align - 1) % 3
            anchor_x = (
                margin("MarginL") * sx
                if column == 0
                else width - margin("MarginR") * sx
                if column == 2
                else width / 2
            )
            anchor_y = (
                height - margin("MarginV") * sy
                if align <= 3
                else margin("MarginV") * sy
                if align >= 7
                else height / 2
            )
        column, row = (align - 1) % 3, (align - 1) // 3
        left = anchor_x - (0.0, block_w / 2, block_w)[column]
        top = anchor_y - (block_h, block_h / 2, 0.0)[row]
        box = (
            round(max(0.0, left / width), 4),
            round(max(0.0, top / height), 4),
            round(min(1.0, (left + block_w) / width), 4),
            round(min(1.0, (top + block_h) / height), 4),
        )
        cues.append(
            LayoutCue(
                _ass_seconds(event.get("Start", "0:0:0")),
                _ass_seconds(event.get("End", "0:0:0")),
                text.strip(),
                box,
            )
        )
    return cues


def burned_caption_file(video: Path) -> Path | None:
    """The caption file a take's captions were burned from: same stem, else the take's newest ``-cap-vN.ass``."""

    same = video.with_suffix(".ass")
    if same.is_file():
        return same
    match = re.match(r"(take-ep\d+-t\d+)-", video.name)
    if not match:
        return None
    found = list(video.parent.glob(f"{match.group(1)}-cap-v*.ass"))

    def version(path: Path) -> int:
        number = re.search(r"-v(\d+)$", path.stem)
        return int(number.group(1)) if number else 0

    return max(found, key=version) if found else None


def sample_times(duration: float, count: int = SAMPLE_FRAMES) -> list[float]:
    """``count`` times spread over the take, away from its first and last frame."""

    return [round(duration * (i + 0.5) / count, 3) for i in range(count)]


def grab_frame(video: Path, seconds: float) -> Image.Image:
    """One frame of ``video`` at ``seconds`` (PNG through a pipe; nothing written)."""

    result = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=False,
    )  # fmt: skip
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"could not read a frame at {seconds:.2f}s of {video.name}")
    return Image.open(BytesIO(result.stdout)).convert("RGB")


def zone_sheet(
    frames: Sequence[tuple[float, Image.Image, Box | None]],
    out: Path,
    *,
    tile_height: int = 480,
) -> Path:
    """Sampled frames side by side, covered zones shaded red, caption boxes outlined."""

    tiles = []
    for seconds, image, box in frames:
        tile = image.resize(
            (max(1, round(image.width * tile_height / image.height)), tile_height)
        ).convert("RGBA")
        shade = Image.new("RGBA", tile.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(shade)
        w, h = tile.size
        for zl, zt, zr, zb in ZONES.values():
            draw.rectangle((zl * w, zt * h, zr * w, zb * h), fill=(230, 40, 40, 90))
        if box is not None:
            draw.rectangle(
                (box[0] * w, box[1] * h, box[2] * w, box[3] * h),
                outline=(40, 220, 255, 255),
                width=3,
            )
        draw.text((6, h - 18), f"{seconds:.1f}s", fill=(255, 255, 255, 255))
        tiles.append(Image.alpha_composite(tile, shade).convert("RGB"))
    sheet = Image.new(
        "RGB", (sum(t.width + 6 for t in tiles), tile_height), (12, 12, 12)
    )
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0))
        x += tile.width + 6
    sheet.save(out)
    return out


def letterbox_file(video: Path) -> bool:
    """True when ``video`` is a letterbox show's 9:16 file: a finish record names it (or, for a joined
    episode, its takes' records) as ``letterbox``."""

    from creation.post.finish_record import latest_finish_record, record_for_file

    resolved = video.expanduser().resolve()
    desk = next((p for p in resolved.parents if (p / "series.json").is_file()), None)
    if desk is None:
        return False
    record = record_for_file(desk, resolved)
    if record is not None:
        return record.letterbox
    joined = re.match(r"episode-ep(\d+)-join", resolved.name)
    if joined:
        first = latest_finish_record(desk, int(joined.group(1)), "t1")
        return bool(first and first.letterbox)
    return False


def check_safe_zones(
    video: Path,
    *,
    sheet_dir: Path | None = None,
    count: int = SAMPLE_FRAMES,
    layout: Path | None = None,
    letterbox: bool | None = None,
) -> SafeZoneReport:
    """Measure captions against the covered zones on sampled frames; write the zone sheet.

    Parameters
    ----------
    video
        The finished take (or any take).
    sheet_dir
        Where the contact sheet goes; default next to ``video``.
    count
        Frames sampled.
    layout
        The burned caption file (:func:`burned_caption_file` finds it by default).
    letterbox
        A letterbox show's 9:16 file (default: :func:`letterbox_file`): its
        captions are checked in the band under the picture.

    Returns
    -------
    SafeZoneReport
        Per-frame caption boxes, the zones they enter and the warnings.
    """

    info = probe_video(video)
    layout = layout or burned_caption_file(video)
    cues = (
        caption_layout(layout, width=info.width, height=info.height)
        if layout is not None
        else []
    )
    grabbed: list[tuple[float, Image.Image, Box | None]] = []
    checks: list[FrameCheck] = []
    low, high = CAPTION_BAND
    below: float | None = None
    band_name = "the house band"
    if letterbox if letterbox is not None else letterbox_file(video):
        from creation.post.delivery_geometry import layout as letterbox_layout

        place = letterbox_layout(info.width, info.height)
        below = place.picture.bottom / info.height
        low = place.caption_highest_top / info.height
        high = place.caption.bottom / info.height
        band_name = "the letterbox band under the picture"
    for seconds in sample_times(info.duration_seconds, count):
        image = grab_frame(video, seconds)
        box = (
            caption_box(image, below=below) if below is not None else caption_box(image)
        )
        measured_by = "pixels"
        if box is None:
            # A white plain caption has no house yellow: take the burned layout's cue at this time.
            showing = next((c for c in cues if c.start <= seconds < c.end), None)
            if showing is not None:
                box, measured_by = showing.box, "layout"
        grabbed.append((seconds, image, box))
        checks.append(
            FrameCheck(
                seconds,
                box,
                zones_entered(box) if box else [],
                bool(box) and not (low - 0.01 <= box[1] and box[3] <= high + 0.01),
                measured_by,
            )
        )
    folder = sheet_dir or video.parent
    sheet = zone_sheet(
        grabbed, next_versioned_path(folder, f"{video.stem}-zones", ".png")
    )
    warnings: list[str] = []
    for name in ZONES:
        hits = [c.seconds for c in checks if name in c.zones]
        if hits:
            warnings.append(
                f"!! caption in {ZONE_WHAT[name]} at {', '.join(f'{s:.1f}s' for s in hits)}: the platform covers it"
            )
    for name in ZONES:
        hits = [cue for cue in cues if name in cue.zones]
        if hits and layout is not None:
            warnings.append(
                f"!! caption cue(s) laid out in {ZONE_WHAT[name]} (measured on `{layout.name}`): "
                + "; ".join(f"{c.start:.2f}-{c.end:.2f}s {c.text[:40]!r}" for c in hits)
            )
    off_band = [c.seconds for c in checks if c.outside_band]
    if off_band:
        warnings.append(
            f"!! caption outside {band_name} ({low:.0%}-{high:.0%} of the height) at "
            f"{', '.join(f'{s:.1f}s' for s in off_band)}"
        )
    if not any(c.caption for c in checks):
        warnings.append(
            "no house caption seen on the sampled frames (a take before captions, or a silent take)"
        )
    warnings.append(FACE_CHECK.format(sheet=sheet.name))
    return SafeZoneReport(video, checks, sheet, warnings, layout, cues)


def newest_finished_take(desk: Path, episode: int, take_id: str) -> Path:
    """The newest file written for this take (the finished one after ``finish``), else the raw take.

    Raises
    ------
    FileNotFoundError
        When the take has no file on the desk.
    """

    takes = desk / f"ep{episode:02d}" / "takes"
    found = [
        p for p in takes.glob(f"take-ep{episode:02d}-{take_id}-*.mp4") if p.is_file()
    ]
    if not found:
        raise FileNotFoundError(f"no file for ep{episode:02d} {take_id} in {takes}")
    return max(found, key=lambda p: p.stat().st_mtime)


def run_review(
    desk: Path | None,
    *,
    episode: int = 1,
    take_id: str = "t1",
    take_file: Path | None = None,
    out: TextIO,
) -> SafeZoneReport:
    """Print the safe-zone check for one take and save the report next to the sheet. Never raises on a finding.

    Parameters
    ----------
    desk
        Series desk (needed when ``take_file`` is not given).
    episode, take_id
        Which take.
    take_file
        The file to check; default the newest file written for the take.
    out
        Where the report prints.

    Returns
    -------
    SafeZoneReport
        What was measured.
    """

    if take_file is None:
        if desk is None:
            raise ValueError("pass --desk (with --episode/--take) or --file")
        take_file = newest_finished_take(desk.expanduser().resolve(), episode, take_id)
    source = take_file.expanduser().resolve()
    report = check_safe_zones(source)
    path = report.sheet.with_suffix(".json")
    path.write_text(json.dumps(report.as_json(), indent=2) + "\n", encoding="utf-8")
    print(
        f"Safe zones on {source.name} ({len(report.frames)} frames sampled):", file=out
    )
    for check in report.frames:
        where = ", ".join(check.zones) if check.zones else "clear"
        box = (
            "no caption"
            if check.caption is None
            else f"caption {check.caption[1]:.0%}-{check.caption[3]:.0%} high: {where}"
        )
        how = " (from the caption file)" if check.measured_by == "layout" else ""
        print(f"  {check.seconds:6.2f}s  {box}{how}", file=out)
    for line in report.warnings:
        print(line, file=out)
    print(f"zone sheet: {report.sheet}", file=out)
    return report


__all__ = [
    "ZONES",
    "LayoutCue",
    "SafeZoneReport",
    "burned_caption_file",
    "caption_box",
    "caption_layout",
    "check_safe_zones",
    "newest_finished_take",
    "run_review",
    "zones_entered",
]
