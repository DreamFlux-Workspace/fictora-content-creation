"""Picture measurements other kit steps share: the hard-cut trace and the blur box."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


from creation.post.media import (
    ffmpeg_bin,
    probe_video,
)

#: Mean luma on ``scale=16:28,tblend=difference`` that marks a cut (inclusive).
FRAME_DIFF_CUT_THRESHOLD = 25.0
#: Tiny analysis size of the frame-difference recipe.
FRAME_DIFF_SCALE = "16:28"
#: Ignore a second spike closer than this to the previous cut.
FRAME_DIFF_MIN_GAP_SECONDS = 0.4

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
