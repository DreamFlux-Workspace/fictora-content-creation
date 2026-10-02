"""Local picture edits on one take: trim, freeze, tempo, soften, blur.

All five are ffmpeg + numpy on this laptop, free, and write a new file; the
take is never overwritten.

- ``trim``   cuts ``A-B`` seconds out, each edge snapped to the strongest
  frame-to-frame change within +/-0.1 s (a real shot change stands far above
  the motion around it), frame-accurate, and says how far everything after the
  cut moves. Run it on the FINISHED take (cues at raw times first).
- ``freeze`` holds the frame at ``at`` for ``hold`` seconds over the picture
  that was there: same length, same frame count, sound copied, so lines, cues
  and captions stay where they were.
- ``tempo``  changes picture and sound speed together (``setpts`` / ``atempo``,
  pitch kept). A ``start``/``end`` window speeds only that span; the rest stays
  at 1x. Without a window the whole timeline changes by the factor.
- ``soften`` finds hard cuts with a scaled ``tblend`` difference trace (H3 cell
  seams that ffmpeg scene detect misses) and softens each in place: the last
  pre-cut frame is held over the new shot and faded out over 0.33 s. Same
  length, sound copied.
- ``blur``   Gaussian-blurs pixel boxes of the picture inside a time window
  (garbled text the video invented): crop, ``gblur``, overlay back with
  ``enable=between(t,from,to)``. Same length, sound copied. A patch.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from creation.ops.folder import next_versioned_path
from creation.post.media import (
    decode_frames,
    ffmpeg_bin,
    keep_cover_args,
    probe_video,
    run_ffmpeg,
    video_streams,
)

# --- trim ---------------------------------------------------------------------------------------

#: How far from the asked time a shot change is looked for.
SNAP_WINDOW_SECONDS = 0.1
#: Frame-difference analysis size (9:16).
TRIM_ANALYSIS_SIZE = (96, 168)
#: A shot change moves the mean pixel at least this much (0-255)...
CUT_MIN_DIFF = 12.0
#: ...and at least this many times the take's typical frame-to-frame change.
CUT_MIN_RATIO = 3.0

# --- soften / cut trace -------------------------------------------------------------------------

#: Hold the last pre-cut frame and fade it out over this long.
SOFTEN_SECONDS = 0.33
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


# ================================================================================================
# trim
# ================================================================================================


@dataclass(frozen=True)
class Snap:
    """Where one end of the cut landed.

    Parameters
    ----------
    asked
        The time the operator gave.
    frame
        The frame the cut is made at (the first frame removed, or the first kept after).
    seconds
        ``frame / fps``.
    snapped
        True when a real shot change was found within the window.
    strength
        The frame difference at that frame (0-255 mean).
    """

    asked: float
    frame: int
    seconds: float
    snapped: bool
    strength: float

    def one_line(self) -> str:
        """``10.210 s -> 10.167 s (frame 244, shot change)``."""

        how = (
            "shot change"
            if self.snapped
            else "no shot change within ±0.1 s: nearest frame"
        )
        return f"{self.asked:.3f} s -> {self.seconds:.3f} s (frame {self.frame}, {how})"


@dataclass(frozen=True)
class TrimResult:
    """What ``trim`` did."""

    source: Path
    output: Path
    start: Snap
    end: Snap
    fps: float
    duration_before: float

    @property
    def removed_seconds(self) -> float:
        """Length cut out."""

        return round((self.end.frame - self.start.frame) / self.fps, 3)

    def shift_line(self) -> str:
        """What to do with everything placed on the old file."""

        return (
            f"Removed {self.removed_seconds:.3f} s ({self.start.seconds:.3f}-{self.end.seconds:.3f} s). "
            f"Cues, voice lines and captions at or after {self.end.seconds:.3f} s on the old file move "
            f"{self.removed_seconds:.3f} s earlier; anything inside the cut is gone. New length "
            f"{self.duration_before - self.removed_seconds:.3f} s."
        )


def frame_differences(take: Path) -> tuple[float, npt.NDArray[np.float64]]:
    """Mean absolute difference between each frame and the one before it.

    Parameters
    ----------
    take
        Video.

    Returns
    -------
    tuple[float, numpy.ndarray]
        Frame rate, and ``diffs[i]`` = change from frame ``i - 1`` to frame ``i`` (``diffs[0] = 0``).
    """

    info = probe_video(take)
    width, height = TRIM_ANALYSIS_SIZE
    frames = decode_frames(take, width=width, height=height)
    diffs = np.zeros(len(frames), dtype=np.float64)
    if len(frames) > 1:
        diffs[1:] = np.abs(frames[1:] - frames[:-1]).mean(axis=(1, 2, 3))
    return float(info.fps or 24.0), diffs


def snap_to_shot_change(
    asked: float,
    *,
    fps: float,
    diffs: npt.NDArray[np.float64],
    window: float = SNAP_WINDOW_SECONDS,
) -> Snap:
    """Snap a time to the strongest shot change within ``window``, else to the nearest frame.

    Parameters
    ----------
    asked
        Seconds.
    fps
        Frame rate.
    diffs
        From :func:`frame_differences`.
    window
        Search half-width in seconds.

    Returns
    -------
    Snap
        The frame the cut is made at.
    """

    last = len(diffs)
    nearest = min(max(int(round(asked * fps)), 0), last)
    if nearest in (0, last):
        return Snap(asked, nearest, round(nearest / fps, 3), False, 0.0)
    reach = int(np.floor(window * fps + 1e-6))
    candidates = [
        index
        for index in range(nearest - reach, nearest + reach + 1)
        if 1 <= index < last
    ]
    best = max(candidates, key=lambda index: (diffs[index], -abs(index - nearest)))
    typical = float(np.median(diffs[1:])) if last > 1 else 0.0
    strength = float(diffs[best])
    if strength >= max(CUT_MIN_DIFF, CUT_MIN_RATIO * typical):
        return Snap(asked, best, round(best / fps, 3), True, round(strength, 2))
    return Snap(
        asked, nearest, round(nearest / fps, 3), False, round(float(diffs[nearest]), 2)
    )


def parse_cut(raw: str) -> tuple[float, float]:
    """Parse ``A-B`` seconds.

    Parameters
    ----------
    raw
        ``10.17-12.15``.

    Returns
    -------
    tuple[float, float]
        Start and end.

    Raises
    ------
    ValueError
        When it does not parse or ``B <= A``.
    """

    match = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*-\s*([0-9]*\.?[0-9]+)\s*", raw)
    if not match:
        raise ValueError(f"--cut needs A-B seconds, e.g. 10.17-12.15: {raw!r}")
    start, end = float(match.group(1)), float(match.group(2))
    if end <= start:
        raise ValueError("--cut end must be after its start")
    return start, end


def plan_trim(take: Path, cut: tuple[float, float]) -> tuple[Snap, Snap, float, float]:
    """Snap both ends of ``A-B`` on ``take`` and check the cut, without writing anything.

    Parameters
    ----------
    take
        The take the operator named.
    cut
        ``(A, B)`` seconds on the take.

    Returns
    -------
    tuple[Snap, Snap, float, float]
        The first frame removed, the first frame kept after, the frame rate and the take's length.

    Raises
    ------
    ValueError
        When the cut is outside the take or removes all of it.
    """

    info = probe_video(take)
    start, end = cut
    if start < 0 or end > info.duration_seconds + 0.05:
        raise ValueError(
            f"--cut {start}-{end} is outside the take (0-{info.duration_seconds:.2f} s)"
        )
    fps, diffs = frame_differences(take)
    first = snap_to_shot_change(start, fps=fps, diffs=diffs)
    after = snap_to_shot_change(end, fps=fps, diffs=diffs)
    if after.frame <= first.frame:
        raise ValueError(
            f"the cut snapped to nothing ({first.one_line()}; {after.one_line()})"
        )
    if first.frame == 0 and after.frame >= len(diffs):
        raise ValueError("the cut removes the whole take")
    return first, after, fps, info.duration_seconds


def cut_frames(take: Path, out: Path, *, begin: int, stop: int, fps: float) -> Path:
    """Remove frames ``begin`` to ``stop - 1`` (and the sound under them) from ``take`` into ``out``.

    Parameters
    ----------
    take
        Video (never overwritten).
    out
        New file.
    begin, stop
        First frame removed and first frame kept after it, at ``fps``.
    fps
        The frame rate the frame numbers count at.

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; trim never overwrites")
    info = probe_video(take)
    picture, cover = video_streams(take)
    t_begin, t_stop = begin / fps, stop / fps
    video = ["-map", f"0:v:{picture}",
             "-filter:v:0", f"select='not(between(n\\,{begin}\\,{stop - 1}))',setpts=N/FRAME_RATE/TB",
             "-r:v:0", f"{fps:g}", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p"]  # fmt: skip
    audio = (
        ["-map", "0:a:0", "-af", f"aselect='not(gte(t\\,{t_begin:.6f})*lt(t\\,{t_stop:.6f}))',asetpts=N/SR/TB",
         "-c:a", "aac", "-b:a", "192k"]
        if info.has_audio
        else ["-an"]
    )  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    # The attached cover (finish's thumbnail) is copied across as it was.
    run_ffmpeg(["-i", str(take), *video, *audio, *keep_cover_args(cover), str(out)])
    return out


def trim_take(take: Path, cut: tuple[float, float], out: Path) -> TrimResult:
    """Cut ``A-B`` out of a take on real shot changes, frame-accurately, into a new file.

    Parameters
    ----------
    take
        The take (never overwritten).
    cut
        ``(A, B)`` seconds on the take.
    out
        New file.

    Returns
    -------
    TrimResult
        Both snapped ends, the removed length and the output.

    Raises
    ------
    ValueError
        When the cut is outside the take or removes all of it.
    FileExistsError
        When ``out`` exists.
    """

    first, after, fps, before = plan_trim(take, cut)
    cut_frames(take, out, begin=first.frame, stop=after.frame, fps=fps)
    return TrimResult(
        source=take,
        output=out,
        start=first,
        end=after,
        fps=fps,
        duration_before=before,
    )


def shift_time(value: float, start: float, end: float) -> float | None:
    """Where a time on the old file lands after ``start-end`` is cut; ``None`` inside the cut.

    Parameters
    ----------
    value
        Seconds on the old file.
    start, end
        The cut.

    Returns
    -------
    float | None
        Seconds on the new file.
    """

    if value < start:
        return value
    if value >= end:
        return round(value - (end - start), 3)
    return None


def shift_timed_items(
    items: list[dict[str, Any]], start: float, end: float
) -> tuple[list[dict[str, Any]], list[str]]:
    """Shift ``[{start, end?, ...}]`` (captions, cues) for a cut; drop what was inside it.

    Parameters
    ----------
    items
        Timed objects.
    start, end
        The cut on the old file.

    Returns
    -------
    tuple[list[dict[str, Any]], list[str]]
        The shifted items and one note per item dropped or clipped.
    """

    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    for number, item in enumerate(items, start=1):
        begin = float(item["start"])
        finish = float(item["end"]) if item.get("end") is not None else None
        new_begin = shift_time(begin, start, end)
        new_finish = shift_time(finish, start, end) if finish is not None else None
        moved = dict(item)
        if new_begin is None:
            if finish is None or finish <= end:
                notes.append(
                    f"item {number} ({begin:.2f}s) was inside the cut: dropped"
                )
                continue
            new_begin = start
            notes.append(
                f"item {number} started inside the cut: now starts at the cut ({start:.3f}s)"
            )
        if finish is not None and new_finish is None:
            new_finish = start
            notes.append(
                f"item {number} ran into the cut: now ends at the cut ({start:.3f}s)"
            )
        moved["start"] = new_begin
        if finish is not None:
            moved["end"] = new_finish
        kept.append(moved)
    return kept, notes


def shift_json_file(path: Path, start: float, end: float) -> tuple[Path, list[str]]:
    """Write a shifted copy of a cues or captions JSON list beside it (``<stem>-trim-vN.json``).

    Parameters
    ----------
    path
        JSON list of objects with ``start`` (and optional ``end``).
    start, end
        The cut on the old file.

    Returns
    -------
    tuple[Path, list[str]]
        The new file and the notes.

    Raises
    ------
    ValueError
        When the file is not a list of timed objects.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) and "start" in item for item in payload
    ):
        raise ValueError(f"{path} must be a JSON list of objects with a start")
    shifted, notes = shift_timed_items(payload, start, end)
    stem = re.sub(r"-v\d+$", "", path.stem)
    out = next_versioned_path(path.parent, f"{stem}-trim", ".json")
    out.write_text(
        json.dumps(shifted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out, notes


# ================================================================================================
# freeze
# ================================================================================================


@dataclass(frozen=True)
class FreezeResult:
    """What ``freeze`` wrote.

    Parameters
    ----------
    output
        The new file.
    at_seconds
        Where the held frame was taken from, snapped to the frame grid.
    hold_seconds
        How long it is held.
    duration_seconds
        Duration of the output (the same as the take).
    """

    output: Path
    at_seconds: float
    hold_seconds: float
    duration_seconds: float

    def one_line(self) -> str:
        """``Freeze at 1.25s held 0.60s -> file (duration 15.00s, audio timeline unchanged)``."""

        return (
            f"Freeze at {self.at_seconds:.2f}s held {self.hold_seconds:.2f}s -> `{self.output.name}` "
            f"(duration {self.duration_seconds:.2f}s, audio timeline unchanged)"
        )


def _rate(fps: float) -> str:
    fraction = Fraction(fps).limit_denominator(1001)
    return f"{fraction.numerator}/{fraction.denominator}"


def freeze_frame_chain(
    *, input_label: str, frame_seconds: float, hold_seconds: float, frame_rate: str, frame_filter: str,
    output_label: str,
) -> str:  # fmt: skip
    """Return the ffmpeg chain that holds one source frame for a fixed time.

    The frame is repeated with ``loop``: ``tpad=stop_mode=clone`` after
    ``trim=end_frame=1`` does not clone on ffmpeg 7, so a freeze came out as
    one frame and the cut came up short.

    Parameters
    ----------
    input_label
        Video pad to read, without brackets (``0:v``).
    frame_seconds
        Source time of the held frame.
    hold_seconds
        How long the frame is held.
    frame_rate
        Output frame rate as ffmpeg reads it (``24/1``).
    frame_filter
        Filters applied to the still before it is held.
    output_label
        Output pad, without brackets.

    Returns
    -------
    str
        One ``filter_complex`` chain ending at ``[output_label]``.
    """

    hold = f"{hold_seconds:.6f}"
    numerator, _, denominator = frame_rate.partition("/")
    rate = float(numerator) / float(denominator or 1)
    repeats = max(1, math.ceil(hold_seconds * rate) + 1)
    return (
        f"[{input_label}]trim=start={frame_seconds:.6f},"
        "setpts=PTS-STARTPTS,trim=end_frame=1,setpts=PTS-STARTPTS,"
        f"{frame_filter},loop=loop={repeats}:size=1:start=0,setpts=N/({frame_rate})/TB,"
        f"fps={frame_rate},trim=duration={hold},setpts=PTS-STARTPTS[{output_label}]"
    )


def freeze_frame(take: Path, out: Path, *, at: float, hold: float) -> FreezeResult:
    """Hold the frame at ``at`` for ``hold`` seconds, over the picture that was there. The audio is copied.

    Nothing is inserted: the take keeps its length, its frame count and its
    sound, so lines, cues and captions placed on it stay where they were.

    Parameters
    ----------
    take
        Video.
    out
        New file.
    at
        Seconds into the take: the frame to hold, and where the hold starts.
    hold
        Seconds to hold it (0.6 is a comic beat).

    Returns
    -------
    FreezeResult
        The file and what was held.

    Raises
    ------
    ValueError
        When ``hold`` is not positive, or the hold does not fit inside the take.
    FileExistsError
        When ``out`` exists.
    """

    info = probe_video(take)
    fps = info.fps or 24.0
    total = info.duration_seconds
    if hold <= 0:
        raise ValueError("--hold must be more than 0 seconds")
    frame = 1.0 / fps
    start = round(at * fps) / fps
    if start < 0 or start >= total - frame:
        raise ValueError(f"--at {at:.2f}s is outside the {total:.2f}s take")
    if start + hold > total + frame / 2:
        raise ValueError(
            f"a {hold:.2f}s hold from {start:.2f}s runs past the {total:.2f}s take; the most that fits is "
            f"{total - start:.2f}s"
        )
    if out.exists():
        raise FileExistsError(f"{out} exists; freeze never overwrites")
    end = min(start + hold, total)
    picture, cover = video_streams(take)
    source = f"0:v:{picture}"
    rate = _rate(fps)
    normalize = f"fps={rate},format=yuv420p,setsar=1"
    parts: list[str] = []
    labels: list[str] = []
    if start > 0:
        parts.append(
            f"[{source}]trim=start=0:end={start:.6f},setpts=PTS-STARTPTS,{normalize}[head]"
        )
        labels.append("[head]")
    parts.append(
        freeze_frame_chain(input_label=source, frame_seconds=start, hold_seconds=end - start, frame_rate=rate,
                           frame_filter="format=yuv420p,setsar=1", output_label="hold")
    )  # fmt: skip
    labels.append("[hold]")
    if end < total - frame / 2:
        parts.append(
            f"[{source}]trim=start={end:.6f},setpts=PTS-STARTPTS,{normalize}[tail]"
        )
        labels.append("[tail]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[v]")
    args = ["-i", str(take), "-filter_complex", ";".join(parts), "-map", "[v]"]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    args += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
    args += [*keep_cover_args(cover), "-t", f"{total:.3f}", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args)
    return FreezeResult(
        output=out,
        at_seconds=start,
        hold_seconds=end - start,
        duration_seconds=probe_video(out).duration_seconds,
    )


# ================================================================================================
# tempo
# ================================================================================================


def change_tempo(
    take: Path,
    out: Path,
    *,
    factor: float = SLOW_TEMPO,
    start: float | None = None,
    end: float | None = None,
) -> Path:
    """Change picture and sound speed together (``setpts`` / ``atempo``, pitch kept).

    Parameters
    ----------
    take
        Video.
    out
        New file.
    factor
        Speed factor: 0.9 plays 10% slower (the take gets longer), 1.1 faster.
    start
        Seconds where a partial speed-up starts. Requires ``end``. Lines inside
        the window speed with it, so the window sits on the action.
    end
        Seconds where the partial speed-up ends.

    Returns
    -------
    Path
        The new file.

    Raises
    ------
    ValueError
        When ``factor`` is outside 0.5-2.0, only one of ``start`` and ``end`` is
        set, or the window does not fit the take.
    FileExistsError
        When ``out`` exists.
    """

    if not 0.5 <= factor <= 2.0:
        raise ValueError("tempo factor must be within 0.5-2.0")
    if (start is None) != (end is None):
        raise ValueError(
            "tempo --from and --to go together: speed the action; lines outside the window stay at 1x"
        )
    if out.exists():
        raise FileExistsError(f"{out} exists; tempo never overwrites")
    info = probe_video(take)
    picture, cover = video_streams(take)
    total = info.duration_seconds
    source = f"0:v:{picture}"
    whole = start is None or (start <= 0.02 and end is not None and end >= total - 0.02)
    if not whole:
        assert start is not None and end is not None
        if start < 0 or end <= start or end > total + 0.05:
            raise ValueError(
                f"tempo window {start:g}-{end:g}s does not fit the {total:.2f}s take"
            )
        end = min(end, total)
        spans: list[tuple[float, float, float]] = []
        if start > 0.02:
            spans.append((0.0, start, 1.0))
        spans.append((start, end, factor))
        if end < total - 0.02:
            spans.append((end, total, 1.0))
        parts: list[str] = []
        vlabels: list[str] = []
        alabels: list[str] = []
        for index, (span_start, span_end, speed) in enumerate(spans):
            pts = (
                "setpts=PTS-STARTPTS"
                if speed == 1
                else f"setpts=(PTS-STARTPTS)/{speed}"
            )
            parts.append(
                f"[{source}]trim=start={span_start:.6f}:end={span_end:.6f},{pts},fps={HOUSE_FPS:g}[v{index}]"
            )
            vlabels.append(f"[v{index}]")
            if info.has_audio:
                tempo = "" if speed == 1 else f",atempo={speed}"
                parts.append(
                    f"[0:a]atrim=start={span_start:.6f}:end={span_end:.6f},asetpts=PTS-STARTPTS{tempo}[a{index}]"
                )
                alabels.append(f"[a{index}]")
        parts.append(f"{''.join(vlabels)}concat=n={len(vlabels)}:v=1:a=0[v]")
        if info.has_audio:
            parts.append(f"{''.join(alabels)}concat=n={len(alabels)}:v=0:a=1[a]")
        graph = ";".join(parts)
    else:
        graph = f"[{source}]setpts=PTS/{factor},fps={HOUSE_FPS:g}[v]"
        if info.has_audio:
            graph += f";[0:a]atempo={factor}[a]"
    maps = ["-map", "[v]"]
    if info.has_audio:
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(take), "-filter_complex", graph, *maps,
                "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", *keep_cover_args(cover), str(out)])  # fmt: skip
    return out


# ================================================================================================
# soften
# ================================================================================================


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


def measure_cuts(take: Path, *, skip_head_frames: int = 0) -> tuple[float, ...]:
    """Hard-cut times from the scaled ``tblend`` difference trace (scene detect is blind on H3 cell seams).

    Parameters
    ----------
    take
        Video.
    skip_head_frames
        Ignore a cut inside the first N+1 frames (a board-leak jump).

    Returns
    -------
    tuple[float, ...]
        Cut times in seconds.

    Raises
    ------
    RuntimeError
        When the analysis fails.
    """

    run = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-i", str(take), "-vf",
         f"scale={FRAME_DIFF_SCALE},format=gray,tblend=all_mode=difference,signalstats,metadata=print:file=-",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if run.returncode != 0:
        raise RuntimeError(f"ffmpeg cut trace failed: {(run.stderr or '')[-400:]}")
    cuts = frame_difference_cut_times(parse_frame_motion(run.stdout))
    if not skip_head_frames:
        return cuts
    floor = (skip_head_frames + 1) / (probe_video(take).fps or 24.0) + 1e-6
    return tuple(time for time in cuts if time > floor)


def soften_seams(
    take: Path, out: Path, cuts: tuple[float, ...], *, seconds: float = SOFTEN_SECONDS
) -> Path:
    """Soften hard cuts in place: hold the last pre-cut frame and fade it out over ``seconds``.

    Duration and lip-sync are preserved; audio is copied.

    Parameters
    ----------
    take
        Video.
    out
        New file.
    cuts
        Cut times in seconds (at least one).
    seconds
        Fade length.

    Returns
    -------
    Path
        The softened file.

    Raises
    ------
    ValueError
        When ``cuts`` is empty.
    FileExistsError
        When ``out`` exists.
    """

    if not cuts:
        raise ValueError("no cuts to soften")
    if out.exists():
        raise FileExistsError(f"{out} exists; soften never overwrites")
    info = probe_video(take)
    picture, cover = video_streams(take)
    fps = info.fps or 24.0
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        inputs = ["-i", str(take)]
        graph: list[str] = []
        last = f"0:v:{picture}"
        for index, cut in enumerate(sorted(cuts), start=1):
            still = Path(tmp) / f"cut{index}.png"
            run_ffmpeg(["-ss", f"{max(0.0, cut - 1 / fps):.4f}", "-i", str(take), "-map", f"0:v:{picture}",
                        "-frames:v", "1", "-update", "1", str(still)])  # fmt: skip
            inputs += ["-loop", "1", "-framerate", f"{fps:g}", "-i", str(still)]
            graph.append(
                f"[{index}:v]format=yuva420p,fade=t=out:st={cut:.4f}:d={seconds}:alpha=1[o{index}]"
            )
            graph.append(
                f"[{last}][o{index}]overlay=eof_action=pass:enable='between(t,{cut:.4f},{cut + seconds:.4f})'[v{index}]"
            )
            last = f"v{index}"
        args = [*inputs, "-filter_complex", ";".join(graph), "-map", f"[{last}]"]
        if info.has_audio:
            args += ["-map", "0:a", "-c:a", "copy"]
        args += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
        args += [
            *keep_cover_args(cover),
            "-t",
            f"{info.duration_seconds:.3f}",
            str(out),
        ]
        run_ffmpeg(args)
    return out


# ================================================================================================
# blur
# ================================================================================================

#: Gaussian sigma of the blur (pixels of the take): what hid garbled signs on Hanakaze ep 4 / ep 6.
BLUR_SIGMA = 20.0
_BOX = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")


@dataclass(frozen=True)
class BlurBox:
    """One rectangle to blur, in pixels of the take (``x``, ``y`` = top-left corner)."""

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


def parse_box(text: str) -> BlurBox:
    """Read ``x,y,w,h`` (whole pixels) into a :class:`BlurBox`.

    Parameters
    ----------
    text
        ``--box`` value, e.g. ``120,340,260,90``.

    Returns
    -------
    BlurBox
        The box.

    Raises
    ------
    ValueError
        When it is not four whole numbers or the width or height is 0.
    """

    match = _BOX.match(text)
    if not match:
        raise ValueError(
            f"--box {text!r}: expected x,y,w,h in whole pixels of the take, e.g. 120,340,260,90"
        )
    box = BlurBox(*(int(value) for value in match.groups()))
    if box.w <= 0 or box.h <= 0:
        raise ValueError(f"--box {text!r}: width and height must be more than 0")
    return box


def _even_box(box: BlurBox, width: int, height: int) -> BlurBox:
    """Grow ``box`` outward to even edges (4:2:0 chroma), never past the frame."""

    x0, y0 = box.x - box.x % 2, box.y - box.y % 2
    x1 = min(width, box.x + box.w + (box.x + box.w) % 2)
    y1 = min(height, box.y + box.h + (box.y + box.h) % 2)
    return BlurBox(x0, y0, x1 - x0, y1 - y0)


@dataclass(frozen=True)
class BlurResult:
    """What :func:`blur_boxes` wrote."""

    output: Path
    boxes: tuple[BlurBox, ...]
    start_seconds: float
    end_seconds: float
    strength: float
    feather: int

    def one_line(self) -> str:
        """Report line."""

        boxes = " ".join(box.label() for box in self.boxes)
        edge = (
            f", feathered {self.feather} px outside each box"
            if self.feather
            else ", hard edge"
        )
        return (
            f"blurred {len(self.boxes)} box(es) [{boxes}] {self.start_seconds:.2f}-{self.end_seconds:.2f}s "
            f"(sigma {self.strength:g}{edge}) -> {self.output.name}; length and sound unchanged"
        )


def blur_boxes(take: Path, out: Path, boxes: tuple[BlurBox, ...], *, start: float, end: float,
               strength: float = BLUR_SIGMA, feather: int = 0) -> BlurResult:  # fmt: skip
    """Blur rectangles of the picture between ``start`` and ``end`` seconds. The audio is copied.

    Each box is cropped from the picture, Gaussian-blurred (``gblur``) and laid
    back over itself with ``overlay`` enabled only inside the window, so the
    take keeps its length, its frame count and its sound. The box is fully
    blurred to its edge. ``feather`` adds a soft ramp OUTSIDE the box (the blur
    fades out over that many pixels), so the box itself stays as obscured as
    with the hard edge: a feather that eats into the box leaves text readable.

    Parameters
    ----------
    take
        Video.
    out
        New file.
    boxes
        Rectangles in pixels of the take; each must lie inside the frame.
        Edges are grown outward to even pixels (4:2:0 chroma).
    start, end
        Seconds into the take: the blur shows from ``start`` to ``end``.
    strength
        Gaussian sigma in pixels (default 20).
    feather
        Pixels of soft edge outside each box (0 = hard edge, the default).

    Returns
    -------
    BlurResult
        The file, the boxes as blurred, and the window.

    Raises
    ------
    ValueError
        When there is no box, a box runs outside the frame, the window is empty
        or outside the take, or ``strength`` / ``feather`` is out of range.
    FileExistsError
        When ``out`` exists.
    """

    if not boxes:
        raise ValueError("blur needs at least one --box")
    if not 0 < strength <= 200:
        raise ValueError("--strength (blur sigma) must be within 0-200")
    if feather < 0:
        raise ValueError("--feather must be 0 or more pixels")
    info = probe_video(take)
    width, height, total = info.width, info.height, info.duration_seconds
    for box in boxes:
        if box.x < 0 or box.y < 0 or box.x + box.w > width or box.y + box.h > height:
            raise ValueError(
                f"--box {box.label()} runs outside the {width}x{height} frame "
                f"(x+w must be <= {width}, y+h <= {height})"
            )
    if start < 0 or end <= start:
        raise ValueError(
            f"--from {start:g} / --to {end:g}: the window must start at 0 or later and end after it starts"
        )
    if start >= total:
        raise ValueError(
            f"--from {start:.2f}s is past the end of the {total:.2f}s take"
        )
    end = min(end, total)
    if out.exists():
        raise FileExistsError(f"{out} exists; blur never overwrites")
    snapped = tuple(_even_box(box, width, height) for box in boxes)
    picture, cover = video_streams(take)
    labels = [f"c{index}" for index in range(len(snapped))]
    graph = [
        f"[0:v:{picture}]format=yuv420p,split={len(snapped) + 1}[base]{''.join(f'[{x}]' for x in labels)}"
    ]
    last = "base"
    window = f"between(t,{start:.4f},{end:.4f})"
    for index, box in enumerate(snapped):
        pad = feather + feather % 2
        x0, y0 = max(0, box.x - pad), max(0, box.y - pad)
        x1, y1 = min(width, box.x + box.w + pad), min(height, box.y + box.h + pad)
        chain = f"[c{index}]crop={x1 - x0}:{y1 - y0}:{x0}:{y0},gblur=sigma={strength:g}"
        if feather:
            # Alpha 255 on the box, ramping to 0 over `feather` px outside it (never inside).
            left, top = box.x - x0, box.y - y0
            right, bottom = left + box.w - 1, top + box.h - 1
            dx = f"max(max({left}-X,0),max(X-{right},0))"
            dy = f"max(max({top}-Y,0),max(Y-{bottom},0))"
            chain += (
                f",format=yuva444p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)'"
                f":a='255*clip(1-max({dx},{dy})/{feather},0,1)'"
            )
        graph.append(f"{chain}[b{index}]")
        graph.append(f"[{last}][b{index}]overlay={x0}:{y0}:enable='{window}'[v{index}]")
        last = f"v{index}"
    args = ["-i", str(take), "-filter_complex", ";".join(graph), "-map", f"[{last}]"]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    args += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
    args += [*keep_cover_args(cover), "-t", f"{total:.3f}", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args)
    return BlurResult(output=out, boxes=snapped, start_seconds=start, end_seconds=end,
                      strength=strength, feather=feather)  # fmt: skip
