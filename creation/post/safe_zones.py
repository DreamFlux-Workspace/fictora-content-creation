"""``review``: measure the safe zones on a finished take (warn, never block).

TikTok, Reels and Shorts draw their own interface over the video: the top 8%
(tabs, search, camera), the bottom 20% (post caption, username, music) and the
right 12% of the lower two thirds (like, comment, share). The board gate reads
the written placements only; this reads the filmed pixels.

What is measured:

- **captions** (24 Oct 2026, fictora-drama #1029): the burned caption file the
  kit wrote beside the take (``take-epNN-tK-cap-vN.ass``) is sent to the
  server's ``POST /v1/captions/safe-area-check``, which lays out every cue as
  libass draws it and names each cue in a covered zone or (portrait) outside
  the 55-70% caption band, with its words and times. Captions are no longer
  looked for by yellow pixels: that boxed a yellow raincoat and a brass lamp
  as captions on the Pawn Ticket paid test (23 Oct 2026). On an older server
  (no route) or with no connection, the kit reads the file itself
  (:func:`caption_layout`: frame size, style font size, alignment and margins,
  ``\\pos``/``\\an``, each line at the font's measured width) and says so.
  A take with no burned caption file beside it is not checked for captions.
- **faces**: this kit carries no face detector (its dependencies are numpy
  and Pillow only), so faces are not measured. ``review`` writes a contact
  sheet of the sampled frames with the covered zones shaded, for the human to
  check that no face, eyes, mouth or key prop sits in them. The board's
  written placements are no longer flagged: only a measured box is a finding.

A letterbox show's 9:16 file (its finish record says ``letterbox``) is
checked for the covered zones only by the server (its captions sit under the
picture, in their own band); the kit's own reader, the fallback, also checks
that band (y 1385-1536, 72-80% on 1920).

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
#: Two cues of the same words this close (seconds) are reported as one span.
_JOIN_SECONDS = 0.02
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
    #: ``server`` (the server's check of the burned caption file) or ``layout`` (the kit's own reader).
    measured_by: str = "server"


@dataclass(frozen=True)
class LayoutCue:
    """One cue of the burned caption file: when it shows, its text and the box it is laid out in."""

    start: float
    end: float
    text: str
    box: Box
    #: The server's answer: whether the cue sits in the portrait caption band (``None``: the kit's reader).
    in_band: bool | None = None

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
    #: ``server`` or ``kit`` (the fallback reader); ``None`` without a caption file.
    checked_by: str | None = None

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
            "captions_checked_by": self.checked_by,
            "faces_measured": False,
        }


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
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
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


def letterbox_file(video: Path, *, desk: Path | None = None) -> bool:
    """True when ``video`` is a letterbox show's 9:16 file: a finish record names it (or, for a joined
    episode, its takes' records) as ``letterbox``, or it is a reel whose plan says ``letterbox``.

    ``desk`` is the series desk (default: the nearest folder above ``video`` with a ``series.json``).
    """

    from creation.post.finish_record import latest_finish_record, record_for_file

    resolved = video.expanduser().resolve()
    reel = re.match(r"reel-(ep\d+(?:-draft)?-v\d+)\.mp4$", resolved.name)
    if reel:
        plan = resolved.with_name(f"reel-plan-{reel.group(1)}.json")
        if not plan.is_file():
            return False
        try:
            body = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(body, dict) and body.get("letterbox") is True
    if desk is None:
        desk = next(
            (p for p in resolved.parents if (p / "series.json").is_file()), None
        )
    if desk is None:
        return False
    record = record_for_file(desk, resolved)
    if record is not None:
        return record.letterbox
    from creation.post.episode_name import episode_number

    joined = re.match(r"episode-ep(\d+)-join", resolved.name)
    episode = int(joined.group(1)) if joined else episode_number(resolved.name)
    if episode is not None:
        first = latest_finish_record(desk, episode, "t1")
        return bool(first and first.letterbox)
    return False


def _desk_of(video: Path) -> Path | None:
    """The series desk above ``video`` (the nearest folder with a ``series.json``)."""

    return next(
        (
            p
            for p in video.expanduser().resolve().parents
            if (p / "series.json").is_file()
        ),
        None,
    )


def _spans(hits: Sequence[tuple[float, float, str]]) -> str:
    """Cues as ``start-end 'words'``, a run of the same words (a Bold line building up) as one span."""

    merged: list[list[Any]] = []
    for start, end, text in sorted(hits):
        last = merged[-1] if merged else None
        if last is not None and last[2] == text and start <= last[1] + _JOIN_SECONDS:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end, text])
    return "; ".join(f"{a:.2f}-{b:.2f}s {t[:40]!r}" for a, b, t in merged)


def _server_cues(answer: dict[str, Any]) -> list[LayoutCue]:
    cues: list[LayoutCue] = []
    for cue in answer.get("cues") or []:
        box = cue.get("box") or (0.0, 0.0, 0.0, 0.0)
        cues.append(
            LayoutCue(
                round(int(cue.get("start_ms") or 0) / 1000, 3),
                round(int(cue.get("end_ms") or 0) / 1000, 3),
                str(cue.get("text") or ""),
                (float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                bool(cue.get("in_band", True)),
            )
        )
    return cues


def check_safe_zones(
    video: Path,
    *,
    sheet_dir: Path | None = None,
    count: int = SAMPLE_FRAMES,
    layout: Path | None = None,
    letterbox: bool | None = None,
    desk: Path | None = None,
) -> SafeZoneReport:
    """Check the burned captions against the covered zones (the server's check); write the zone sheet.

    Parameters
    ----------
    video
        The finished take (or any take).
    sheet_dir
        Where the contact sheet goes; default next to ``video``.
    count
        Frames sampled for the sheet.
    layout
        The burned caption file (:func:`burned_caption_file` finds it by default).
    letterbox
        A letterbox show's 9:16 file (default: :func:`letterbox_file`).
    desk
        The series desk whose API session asks the server (default: the
        nearest folder above ``video`` with a ``series.json``).

    Returns
    -------
    SafeZoneReport
        Per-frame caption boxes (the cue showing then), the zones they enter and the warnings.
    """

    from creation.caption_server import server_safe_area_check
    from creation.post.episode_name import episode_number

    info = probe_video(video)
    layout = layout or burned_caption_file(video)
    is_letterbox = letterbox if letterbox is not None else letterbox_file(video)
    low, high = CAPTION_BAND
    band_name = "the caption band"
    if is_letterbox:
        from creation.post.delivery_geometry import layout as letterbox_layout

        place = letterbox_layout(info.width, info.height)
        low = place.caption_highest_top / info.height
        high = place.caption.bottom / info.height
        band_name = "the letterbox band under the picture"
    cues: list[LayoutCue] = []
    checked_by: str | None = None
    warnings: list[str] = []
    answer: dict[str, Any] | None = None
    if layout is not None:
        answer = server_safe_area_check(
            layout.read_text(encoding="utf-8"),
            letterbox=is_letterbox,
            desk=desk or _desk_of(video),
            episode=episode_number(video.name) or 1,
        )
        if answer is not None:
            checked_by, cues = "server", _server_cues(answer)
            by_kind: dict[tuple[str, str | None], list[tuple[float, float, str]]] = {}
            for finding in answer.get("findings") or []:
                key = (str(finding.get("kind")), finding.get("zone"))
                by_kind.setdefault(key, []).append(
                    (int(finding.get("start_ms") or 0) / 1000, int(finding.get("end_ms") or 0) / 1000,
                     str(finding.get("text") or ""))
                )  # fmt: skip
            for (kind, zone), hits in by_kind.items():
                where = (
                    f"in {ZONE_WHAT.get(str(zone), str(zone))}: the platform covers it"
                    if kind == "zone"
                    else "outside the caption band (55%-70% of the height)"
                )
                warnings.append(
                    f"!! caption {where} (server check of `{layout.name}`): {_spans(hits)}"
                )
        else:
            checked_by = "kit"
            cues = caption_layout(layout, width=info.width, height=info.height)
            for name in ZONES:
                hits = [(c.start, c.end, c.text) for c in cues if name in c.zones]
                if hits:
                    warnings.append(
                        f"!! caption in {ZONE_WHAT[name]}: the platform covers it "
                        f"(the kit's reading of `{layout.name}`): {_spans(hits)}"
                    )
            off = [
                (c.start, c.end, c.text)
                for c in cues
                if not (low - 0.01 <= c.box[1] and c.box[3] <= high + 0.01)
            ]
            if off:
                warnings.append(
                    f"!! caption outside {band_name} ({low:.0%}-{high:.0%} of the height) "
                    f"(the kit's reading of `{layout.name}`): {_spans(off)}"
                )
    grabbed: list[tuple[float, Image.Image, Box | None]] = []
    checks: list[FrameCheck] = []
    for seconds in sample_times(info.duration_seconds, count):
        image = grab_frame(video, seconds)
        showing = next((c for c in cues if c.start <= seconds < c.end), None)
        box = showing.box if showing is not None else None
        if showing is None:
            outside = False
        elif showing.in_band is not None:
            outside = not is_letterbox and not showing.in_band
        else:
            outside = not (
                low - 0.01 <= showing.box[1] and showing.box[3] <= high + 0.01
            )
        grabbed.append((seconds, image, box))
        checks.append(
            FrameCheck(
                seconds,
                box,
                zones_entered(box) if box else [],
                outside,
                "layout" if checked_by == "kit" else "server",
            )
        )
    folder = sheet_dir or video.parent
    sheet = zone_sheet(
        grabbed, next_versioned_path(folder, f"{video.stem}-zones", ".png")
    )
    if layout is None:
        warnings.append(
            "no burned caption file beside the take: captions not checked "
            "(a take before captions, or one captioned outside the kit)"
        )
    elif not cues:
        warnings.append(f"no caption cues in `{layout.name}` (a silent take)")
    warnings.append(FACE_CHECK.format(sheet=sheet.name))
    return SafeZoneReport(video, checks, sheet, warnings, layout, cues, checked_by)


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
    how = {
        "server": f"captions checked by the server on {report.layout.name if report.layout else ''}",
        "kit": f"captions read by the kit from {report.layout.name if report.layout else ''} (older server)",
    }.get(report.checked_by or "", "no caption file")
    print(
        f"Safe zones on {source.name} ({len(report.frames)} frames sampled; {how}):",
        file=out,
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
    "LayoutCue",
    "SafeZoneReport",
    "burned_caption_file",
    "caption_layout",
    "check_safe_zones",
    "newest_finished_take",
    "run_review",
    "zones_entered",
]
