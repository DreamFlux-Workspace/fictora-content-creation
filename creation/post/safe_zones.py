"""``review``: measure the safe zones on a finished take (warn, never block).

TikTok, Reels and Shorts draw their own interface over the video: the top 8%
(tabs, search, camera), the bottom 20% (post caption, username, music) and the
right 12% of the lower two thirds (like, comment, share). The board gate reads
the written placements only; this reads the filmed pixels.

What is measured, on frames sampled across the take:

- **captions**: the house caption is yellow (``#FFE500``), so its box is found
  by colour on each frame and checked against the covered zones and against
  the house caption band (55-70% of the frame height).
- **faces**: this kit carries no face detector (its dependencies are numpy
  and Pillow only), so faces are not measured. ``review`` writes a contact
  sheet of the sampled frames with the covered zones shaded, for the human to
  check that no face, eyes, mouth or key prop sits in them.

Everything is a warning: ``review`` always exits 0 and changes no take.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, TextIO

import numpy as np
from PIL import Image, ImageDraw

from creation.captions import CAPTION_BAND
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
    "faces: not measured (no face detector in this kit). Look at {sheet}: the covered zones are shaded red; "
    "check no face, eyes, mouth or key prop sits in them."
)

Box = tuple[float, float, float, float]


@dataclass
class FrameCheck:
    """One sampled frame: when, its caption box (fractions) and the zones that box enters."""

    seconds: float
    caption: Box | None
    zones: list[str] = field(default_factory=list)
    outside_band: bool = False


@dataclass
class SafeZoneReport:
    """What ``review`` measured on one file."""

    source: Path
    frames: list[FrameCheck]
    sheet: Path
    warnings: list[str]

    def as_json(self) -> dict[str, Any]:
        """JSON-ready report."""

        return {
            "source": str(self.source),
            "sheet": str(self.sheet),
            "zones": ZONES,
            "frames": [asdict(f) for f in self.frames],
            "warnings": self.warnings,
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


def caption_box(image: Image.Image) -> Box | None:
    """The caption's box as ``(left, top, right, bottom)`` fractions, or ``None`` when no caption shows.

    The house band (about 50–78% of the height) is searched first. A yellow
    lamp or highlight outside that band does not stretch the box. A caption
    that sits in the covered bottom band is still found, on the whole frame,
    so the warning still fires.
    """

    pixels = np.asarray(image.convert("RGB"))
    height = pixels.shape[0]
    mask = caption_mask(pixels)
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


def check_safe_zones(
    video: Path, *, sheet_dir: Path | None = None, count: int = SAMPLE_FRAMES
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

    Returns
    -------
    SafeZoneReport
        Per-frame caption boxes, the zones they enter and the warnings.
    """

    info = probe_video(video)
    grabbed: list[tuple[float, Image.Image, Box | None]] = []
    checks: list[FrameCheck] = []
    for seconds in sample_times(info.duration_seconds, count):
        image = grab_frame(video, seconds)
        box = caption_box(image)
        grabbed.append((seconds, image, box))
        low, high = CAPTION_BAND
        checks.append(
            FrameCheck(
                seconds,
                box,
                zones_entered(box) if box else [],
                bool(box) and not (low - 0.01 <= box[1] and box[3] <= high + 0.01),
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
    off_band = [c.seconds for c in checks if c.outside_band]
    if off_band:
        low, high = CAPTION_BAND
        warnings.append(
            f"!! caption outside the house band ({low:.0%}-{high:.0%} of the height) at "
            f"{', '.join(f'{s:.1f}s' for s in off_band)}"
        )
    if not any(c.caption for c in checks):
        warnings.append(
            "no house caption seen on the sampled frames (a take before captions, or a silent take)"
        )
    warnings.append(FACE_CHECK.format(sheet=sheet.name))
    return SafeZoneReport(video, checks, sheet, warnings)


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
        print(f"  {check.seconds:6.2f}s  {box}", file=out)
    for line in report.warnings:
        print(line, file=out)
    print(f"zone sheet: {report.sheet}", file=out)
    return report


__all__ = [
    "ZONES",
    "SafeZoneReport",
    "caption_box",
    "check_safe_zones",
    "newest_finished_take",
    "run_review",
    "zones_entered",
]
