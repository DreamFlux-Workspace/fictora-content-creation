"""Picture measurements and the punch-in other kit steps share: the hard-cut trace and ``punch_in``.

The take edit commands (``freeze``, ``tempo``, ``soften``, ``zoom``, ``trim --cut``,
``blur``) are take edits on the server (:mod:`creation.post.take_edit`,
:mod:`creation.post.take_tempo`); nothing here is one of them.

- :func:`measure_cuts` finds hard cuts with a scaled ``tblend`` difference trace
  (H3 cell seams that ffmpeg scene detect misses), for review and the join.
- :func:`punch_in` crops a smaller window of the picture around a point and scales
  it back to the full frame (the impact punch-ins of the finish).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


from creation.post.media import (
    ffmpeg_bin,
    keep_cover_args,
    probe_video,
    run_ffmpeg,
    video_streams,
)

# --- soften / cut trace -------------------------------------------------------------------------

#: Mean luma on ``scale=16:28,tblend=difference`` that marks a cut (inclusive).
FRAME_DIFF_CUT_THRESHOLD = 25.0
#: Tiny analysis size of the frame-difference recipe.
FRAME_DIFF_SCALE = "16:28"
#: Ignore a second spike closer than this to the previous cut.
FRAME_DIFF_MIN_GAP_SECONDS = 0.4

# --- tempo --------------------------------------------------------------------------------------

#: The slow cut some shows use.
SLOW_TEMPO = 0.9
#: Output frame rate after a tempo change.
HOUSE_FPS = 24.0

_PTS = re.compile(r"pts_time:(?P<time>[0-9.]+)")
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=(?P<value>[0-9.]+)")


def parse_frame_motion(metadata_text: str) -> tuple[tuple[float, float], ...]:
    """Pull per-frame ``(time, YAVG)`` pairs out of a ``tblend=difference,signalstats`` run.

    Parameters
    ----------
    metadata_text
        ffmpeg's ``metadata=print`` output.

    Returns
    -------
    tuple[tuple[float, float], ...]
        ``(pts_time, mean luma difference)`` per frame, in order.
    """

    rows: list[tuple[float, float]] = []
    pending: float | None = None
    for line in metadata_text.splitlines():
        pts = _PTS.search(line)
        if pts:
            pending = float(pts.group("time"))
            continue
        yavg = _YAVG.search(line)
        if yavg and pending is not None:
            rows.append((pending, float(yavg.group("value"))))
            pending = None
    return tuple(rows)


def frame_difference_cut_times(
    frame_motion: tuple[tuple[float, float], ...],
    *,
    threshold: float = FRAME_DIFF_CUT_THRESHOLD,
    min_gap_seconds: float = FRAME_DIFF_MIN_GAP_SECONDS,
) -> tuple[float, ...]:
    """Return cut times from a scaled ``tblend=difference`` trace.

    Parameters
    ----------
    frame_motion
        ``(pts_time, YAVG)`` pairs from the scaled difference filter.
    threshold
        Inclusive YAVG that counts as a cut.
    min_gap_seconds
        Debounce between accepted cuts.

    Returns
    -------
    tuple[float, ...]
        Cut times in seconds, ascending.
    """

    cuts: list[float] = []
    for time, yavg in frame_motion:
        if time <= 0.0 or yavg < threshold:
            continue
        if cuts and time - cuts[-1] < min_gap_seconds:
            continue
        cuts.append(time)
    return tuple(cuts)


def measure_cuts(
    take: Path,
    *,
    skip_head_frames: int = 0,
    crop: tuple[int, int, int, int] | None = None,
) -> tuple[float, ...]:
    """Hard-cut times from the scaled ``tblend`` difference trace (scene detect is blind on H3 cell seams).

    Parameters
    ----------
    take
        Video.
    skip_head_frames
        Ignore a cut inside the first N+1 frames (a board-leak jump).
    crop
        ``(x, y, width, height)`` of the frame to read (a letterbox file's
        picture); ``None`` reads the whole frame.

    Returns
    -------
    tuple[float, ...]
        Cut times in seconds.

    Raises
    ------
    RuntimeError
        When the analysis fails.
    """

    region = "" if crop is None else f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]},"
    run = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-i", str(take), "-vf",
         f"{region}scale={FRAME_DIFF_SCALE},format=gray,tblend=all_mode=difference,signalstats,metadata=print:file=-",
         "-an", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if run.returncode != 0:
        raise RuntimeError(f"ffmpeg cut trace failed: {(run.stderr or '')[-400:]}")
    cuts = frame_difference_cut_times(parse_frame_motion(run.stdout))
    if not skip_head_frames:
        return cuts
    floor = (skip_head_frames + 1) / (probe_video(take).fps or 24.0) + 1e-6
    return tuple(time for time in cuts if time > floor)


# ================================================================================================
# boxes
# ================================================================================================


@dataclass(frozen=True)
class BlurBox:
    """One rectangle of the picture in pixels of the take (``x``, ``y`` = top-left corner): a prop-text box or a zoom crop."""

    x: int
    y: int
    w: int
    h: int

    def as_list(self) -> list[int]:
        """``[x, y, w, h]`` for a record."""

        return [self.x, self.y, self.w, self.h]

    def label(self) -> str:
        """``x,y,w,h`` as the operator typed it."""

        return f"{self.x},{self.y},{self.w},{self.h}"


# ================================================================================================
# zoom (punch-in)
# ================================================================================================

#: Default punch-in: the picture 15% closer (a crop of 1/1.15 of each side), enough to read as a new angle.
ZOOM_FACTOR = 1.15
#: Closest punch-in: past 2x a 768p take turns visibly soft.
ZOOM_MAX = 2.0


def zoom_crop(
    width: int, height: int, factor: float, center: tuple[int, int] | None = None
) -> BlurBox:
    """The window a punch-in of ``factor`` shows, centred on ``center`` and kept inside the frame.

    The window keeps the frame's shape (``width / factor`` by ``height /
    factor``, rounded down to even pixels for 4:2:0 chroma). A centre near an
    edge slides the window inward instead of showing past the frame.

    Parameters
    ----------
    width, height
        The take's frame size in pixels.
    factor
        How much closer: more than 1.0, at most :data:`ZOOM_MAX`.
    center
        Pixel to zoom on (``x, y``); ``None`` is the middle of the frame.

    Returns
    -------
    BlurBox
        The crop window (``x``, ``y`` = top-left corner, ``w``, ``h``).

    Raises
    ------
    ValueError
        When ``factor`` is out of range or ``center`` lies outside the frame.
    """

    if not 1.0 < factor <= ZOOM_MAX:
        raise ValueError(
            f"--factor {factor:g}: a punch-in is more than 1.0 and at most {ZOOM_MAX:g}"
        )
    cx, cy = center if center is not None else (width // 2, height // 2)
    if not (0 <= cx < width and 0 <= cy < height):
        raise ValueError(f"--center {cx},{cy} lies outside the {width}x{height} frame")
    w = max(2, int(width / factor) // 2 * 2)
    h = max(2, int(height / factor) // 2 * 2)
    x = min(max(0, cx - w // 2), width - w)
    y = min(max(0, cy - h // 2), height - h)
    return BlurBox(x - x % 2, y - y % 2, w, h)


@dataclass(frozen=True)
class ZoomResult:
    """What :func:`punch_in` wrote."""

    output: Path
    factor: float
    crop: BlurBox
    start_seconds: float
    end_seconds: float
    whole_take: bool

    def one_line(self) -> str:
        """Report line."""

        when = (
            "the whole take"
            if self.whole_take
            else f"{self.start_seconds:.2f}-{self.end_seconds:.2f}s"
        )
        return (
            f"punched in {self.factor:g}x on [{self.crop.label()}] for {when} -> {self.output.name}; "
            "length and sound unchanged"
        )


def punch_in(
    take: Path,
    out: Path,
    *,
    factor: float = ZOOM_FACTOR,
    center: tuple[int, int] | None = None,
    start: float | None = None,
    end: float | None = None,
) -> ZoomResult:
    """Punch in on the picture between ``start`` and ``end`` seconds (the whole take without them).

    The window from :func:`zoom_crop` is cropped, scaled back to the full frame
    (lanczos) and laid over the picture with ``overlay`` enabled only inside
    the time window, so the take keeps its size, length, frame count and sound.
    Free, on this laptop: the salvage for a jump cut (punch in on the shot after
    it), or for a stray element at the edge of the frame (zoom away from it).

    Parameters
    ----------
    take
        Video.
    out
        New file.
    factor
        How much closer (default :data:`ZOOM_FACTOR`; more than 1.0, at most
        :data:`ZOOM_MAX`).
    center
        Pixel of the take to zoom on (``x, y``); default the middle.
    start, end
        Seconds into the take; both or neither (neither = the whole take).

    Returns
    -------
    ZoomResult
        The file, the crop window and the time window.

    Raises
    ------
    ValueError
        When the factor or centre is out of range, only one of ``start`` /
        ``end`` is given, or the window is empty or outside the take.
    FileExistsError
        When ``out`` exists.
    """

    if (start is None) != (end is None):
        raise ValueError("zoom --from and --to go together (neither = the whole take)")
    info = probe_video(take)
    width, height, total = info.width, info.height, info.duration_seconds
    crop = zoom_crop(width, height, factor, center)
    whole = start is None
    first = 0.0 if start is None else start
    last = total if end is None else end
    if first < 0 or last <= first:
        raise ValueError(
            f"--from {first:g} / --to {last:g}: the window must start at 0 or later and end after it starts"
        )
    if first >= total:
        raise ValueError(
            f"--from {first:.2f}s is past the end of the {total:.2f}s take"
        )
    last = min(last, total)
    if out.exists():
        raise FileExistsError(f"{out} exists; zoom never overwrites")
    picture, cover = video_streams(take)
    window = f"between(t,{first:.4f},{last:.4f})"
    graph = ";".join(
        [
            f"[0:v:{picture}]format=yuv420p,split=2[base][z]",
            f"[z]crop={crop.w}:{crop.h}:{crop.x}:{crop.y},scale={width}:{height}:flags=lanczos,setsar=1[near]",
            f"[base][near]overlay=0:0:enable='{window}'[v]",
        ]
    )
    args = ["-i", str(take), "-filter_complex", graph, "-map", "[v]"]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    args += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
    args += [*keep_cover_args(cover), "-t", f"{total:.3f}", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args)
    return ZoomResult(output=out, factor=factor, crop=crop, start_seconds=first,
                      end_seconds=last, whole_take=whole)  # fmt: skip
