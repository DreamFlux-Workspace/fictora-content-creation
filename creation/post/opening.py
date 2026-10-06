"""How a file opens and how it ends: warnings, never a stop.

The first second decides whether a viewer stays; the last frame decides
whether they feel the cut. Three opening checks and one ending check, the same
numbers wherever a file opens or ends (an episode's first take, every reel's
first second, a reel's or a joined episode's tail):

- **Dark first frame**: Rec.601 mean luma (0-1) under :data:`DARK_LUMA` on
  frame 0 AND over the first :data:`DARK_WINDOW_SECONDS`.
- **Static opening**: the mean frame-to-frame change (grey RMSE, 0-1, at
  :data:`MEASURE_FPS`) over the first :data:`OPENING_SECONDS` under
  :data:`STATIC_MOTION`. A cut inside the second counts as motion.
- **No face on frame 0**: by the local face detector
  (:mod:`creation.post.faces`), else by the take facts' head count for the
  first shot. Skipped when the opening is silent / visual (its beat has no
  spoken line): a wordless opening may open on a place or a thing.
- **Settled tail**: more than :data:`TAIL_MAX_SECONDS` after the last line or
  action (the last caption or sound event, or the last sample still moving by
  :data:`STATIC_MOTION` or more) before the file ends. The cut should land on
  the peak, not after it settles.

The thresholds sit here as named constants, one table to tune.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from creation.post.faces import FaceReading

#: The opening checked: the first second.
OPENING_SECONDS = 1.0
#: Frame 0 and the mean over this first stretch must both be under DARK_LUMA to read as dark.
DARK_WINDOW_SECONDS = 0.5
#: Mean Rec.601 luma (0-1) under which a frame reads as dark (about 25 of 255).
DARK_LUMA = 0.10
#: Mean frame-to-frame grey RMSE (0-1, at MEASURE_FPS) under which the picture is not moving.
STATIC_MOTION = 0.006
#: More than this after the last line or action, before the end, is a settled tail.
TAIL_MAX_SECONDS = 0.3
#: The measurement's rate and size (the reel's and review's 8 fps at 96x168).
MEASURE_FPS = 8.0
MEASURE_SIZE = (96, 168)
#: How much of a file's end the tail check reads.
TAIL_WINDOW_SECONDS = 3.0

_LUMA = np.asarray([0.299, 0.587, 0.114])


def luma(frames: npt.NDArray[Any]) -> npt.NDArray[np.float64]:
    """Rec.601 luma of RGB frames (0-255) as 0-1, ``(N, H, W)``."""

    return (np.asarray(frames, dtype=np.float64) @ _LUMA) / 255.0


def motion_of(grey: npt.NDArray[np.float64]) -> list[float]:
    """Frame-to-frame RMSE of grey frames; sample 0 takes sample 1's value (no frame before it)."""

    if len(grey) < 2:
        return [0.0] * len(grey)
    moves = np.sqrt(((grey[1:] - grey[:-1]) ** 2).mean(axis=(1, 2))).tolist()
    return [moves[0], *moves]


@dataclass(frozen=True)
class OpeningReading:
    """The first second of a file, as measured.

    Parameters
    ----------
    frame0_luma
        Mean luma of frame 0 (0-1).
    early_luma
        Mean luma over the first :data:`DARK_WINDOW_SECONDS`.
    motion
        Mean frame-to-frame change over the first :data:`OPENING_SECONDS`.
    face
        Frame 0's face score (0-1), ``None`` when not checked.
    faces
        Faces the detector found on frame 0 (``None`` without a detector).
    face_source
        ``detector``, ``head_count`` or ``none``.
    silent_open
        The opening is silent / visual (its beat has no spoken line): no face check.
    """

    frame0_luma: float
    early_luma: float
    motion: float
    face: float | None = None
    faces: int | None = None
    face_source: str = "none"
    silent_open: bool = False
    warnings: tuple[str, ...] = field(default=())

    def as_json(self) -> dict[str, Any]:
        """The plan / review JSON block."""

        return {
            "frame0_luma": round(self.frame0_luma, 4),
            "first_half_second_luma": round(self.early_luma, 4),
            "first_second_motion": round(self.motion, 5),
            "frame0_face": None if self.face is None else round(self.face, 3),
            "frame0_faces": self.faces,
            "face_source": self.face_source,
            "silent_open": self.silent_open,
            "warnings": list(self.warnings),
        }


def opening_reading(
    sample_luma: Sequence[float],
    sample_motion: Sequence[float],
    *,
    rate: float = MEASURE_FPS,
    face: FaceReading | float | None = None,
    face_source: str = "none",
    silent_open: bool = False,
    where: str = "the opening",
) -> OpeningReading:
    """Read the opening from samples taken ``rate`` times a second from frame 0 on (pure).

    Parameters
    ----------
    sample_luma, sample_motion
        Per-sample mean luma and frame-to-frame change (sample 0 is frame 0).
    rate
        Samples a second.
    face
        Frame 0's face: a detector reading, a 0-1 score (the head count), or ``None``.
    face_source
        Where ``face`` came from.
    silent_open
        The opening beat has no spoken line.
    where
        What opens, for the warnings (``ep01 t1``, ``the reel``).

    Returns
    -------
    OpeningReading
        With its warnings filled in (:func:`opening_warnings`).
    """

    half = max(1, int(round(DARK_WINDOW_SECONDS * rate)))
    second = max(2, int(round(OPENING_SECONDS * rate)) + 1)
    lumas = list(sample_luma)
    first = lumas[0] if lumas else 0.0
    early = float(np.mean(lumas[:half])) if lumas else 0.0
    # Motion of sample i is the change into it: samples 1..rate cover the first second.
    moves = list(sample_motion)[1:second] or list(sample_motion)[:1]
    mean_move = float(np.mean(moves)) if moves else 0.0
    if isinstance(face, FaceReading):
        score: float | None = face.score
        count: int | None = face.count
    else:
        score, count = face, None
    reading = OpeningReading(
        frame0_luma=first, early_luma=early, motion=mean_move, face=score,
        faces=count, face_source=face_source if face is not None else "none",
        silent_open=silent_open,
    )  # fmt: skip
    return replace(reading, warnings=tuple(opening_warnings(reading, where=where)))


def opening_warnings(
    reading: OpeningReading, *, where: str = "the opening"
) -> list[str]:
    """The ⚠ lines for an opening (never a stop).

    Parameters
    ----------
    reading
        The measured opening.
    where
        What opens (``ep01 t1``, ``the reel``), for the message.

    Returns
    -------
    list[str]
        Dark, static and no-face warnings that apply.
    """

    warnings: list[str] = []
    if reading.frame0_luma < DARK_LUMA and reading.early_luma < DARK_LUMA:
        warnings.append(
            f"{where} opens dark: frame 0 luma {reading.frame0_luma:.3f}, first {DARK_WINDOW_SECONDS:g} s "
            f"{reading.early_luma:.3f} (under {DARK_LUMA:g}); the first frame is the thumbnail of the scroll: "
            "open on a lit frame, or trim to one"
        )
    if reading.motion < STATIC_MOTION:
        warnings.append(
            f"{where} is static: mean frame-to-frame {reading.motion:.4f} over the first {OPENING_SECONDS:g} s "
            f"(under {STATIC_MOTION:g}); open on movement, or trim the held head"
        )
    if not reading.silent_open and reading.face is not None and reading.face <= 0:
        how = (
            "the face detector found none"
            if reading.face_source == "detector"
            else "the take facts put no named character in the first shot"
        )
        warnings.append(
            f"{where} has no face on frame 0 ({how}); a spoken opening holds better on a face "
            "(a wordless opening on a place or a thing is fine: it is not checked)"
        )
    return warnings


def measure_opening(
    path: Path,
    *,
    detector: Any = None,
    head_count_face: float | None = None,
    silent_open: bool = False,
    where: str = "the opening",
    crop: tuple[int, int, int, int] | None = None,
) -> OpeningReading:
    """Measure a file's first second: luma, motion and frame 0's face.

    Parameters
    ----------
    path
        Video.
    detector
        A face detector (:func:`creation.post.faces.local_detector`); ``None``
        falls back to ``head_count_face``.
    head_count_face
        The take facts' head-count face score for the first shot, used without a detector.
    silent_open
        The opening beat has no spoken line (no face check).
    where
        What opens, for the warnings.
    crop
        ``(x, y, width, height)``: measure only this part of the frame (a
        letterbox file's picture, not its black bands); ``None`` measures it all.

    Returns
    -------
    OpeningReading
        With its warnings.
    """

    from creation.post.faces import FACE_SIZE
    from creation.post.media import decode_frames

    width, height = MEASURE_SIZE
    count = int(OPENING_SECONDS * MEASURE_FPS) + 2
    frames = decode_frames(
        path, width=width, height=height, fps=MEASURE_FPS, max_frames=count,
        crop=crop,
    )  # fmt: skip
    grey = luma(frames)
    lumas = grey.mean(axis=(1, 2)).tolist() if len(grey) else []
    face: FaceReading | float | None = None
    source = "none"
    if detector is not None:
        first = decode_frames(
            path, width=FACE_SIZE[0], height=FACE_SIZE[1], max_frames=1,
            crop=crop,
        )  # fmt: skip
        if len(first):
            face, source = detector(first[0].astype(np.uint8)), "detector"
    elif head_count_face is not None:
        face, source = head_count_face, "head_count"
    return opening_reading(
        lumas, motion_of(grey), face=face, face_source=source,
        silent_open=silent_open, where=where,
    )  # fmt: skip


@dataclass(frozen=True)
class TailReading:
    """The end of a file: how long it runs on after the last line or action.

    Parameters
    ----------
    duration
        The file's (or segment's) end, seconds.
    last_mark
        The last caption or sound event's end (``None`` when none is known).
    last_motion
        The last sample still moving (``None`` when none in the window read).
    """

    duration: float
    last_mark: float | None
    last_motion: float | None

    @property
    def settled_seconds(self) -> float:
        """Seconds from the last line or action to the end."""

        marks = [m for m in (self.last_mark, self.last_motion) if m is not None]
        if not marks:
            return 0.0
        return max(0.0, self.duration - max(marks))

    def as_json(self) -> dict[str, Any]:
        """The plan / review JSON block."""

        return {
            "end_s": round(self.duration, 3),
            "last_line_or_event_s": None
            if self.last_mark is None
            else round(self.last_mark, 3),
            "last_motion_s": None
            if self.last_motion is None
            else round(self.last_motion, 3),
            "settled_tail_s": round(self.settled_seconds, 3),
        }


def tail_reading(
    sample_seconds: Sequence[float],
    sample_motion: Sequence[float],
    *,
    end: float,
    last_mark: float | None,
    start: float = 0.0,
) -> TailReading:
    """The tail from samples (pure): the last moving sample between ``start`` and ``end``.

    A sample moving by :data:`STATIC_MOTION` or more is action up to that
    sample (its change is the change INTO it).
    """

    moving = [
        float(t)
        for t, m in zip(sample_seconds, sample_motion, strict=False)
        if start <= t < end and m >= STATIC_MOTION
    ]
    return TailReading(
        duration=end,
        last_mark=last_mark,
        last_motion=min(end, max(moving)) if moving else None,
    )


def tail_warning(reading: TailReading, *, what: str = "the cut") -> str | None:
    """A ⚠ when more than :data:`TAIL_MAX_SECONDS` runs on after the last line or action.

    Parameters
    ----------
    reading
        The measured tail.
    what
        What ends (``the reel``, ``ep01``), for the message.

    Returns
    -------
    str | None
        The warning, or ``None``.
    """

    settled = reading.settled_seconds
    if settled <= TAIL_MAX_SECONDS + 1e-6:
        return None
    return (
        f"{what} runs {settled:.2f} s past its last line or action (settled from "
        f"{reading.duration - settled:.2f} s, over {TAIL_MAX_SECONDS:g} s): end it hard on the peak, "
        "trim the tail"
    )


def measure_tail(
    path: Path,
    *,
    last_mark: float | None,
    window: float = TAIL_WINDOW_SECONDS,
    crop: tuple[int, int, int, int] | None = None,
) -> TailReading:
    """Measure the last ``window`` seconds of a file for a settled tail.

    Parameters
    ----------
    path
        Video.
    last_mark
        The last line's or sound event's end, seconds into the file (``None`` when unknown).
    window
        How much of the end to read.
    crop
        ``(x, y, width, height)``: read only this part of the frame (a letterbox file's picture).

    Returns
    -------
    TailReading
        The tail.
    """

    from creation.post.media import decode_frames, probe_video

    duration = probe_video(path).duration_seconds
    start = max(0.0, duration - window)
    width, height = MEASURE_SIZE
    frames = decode_frames(
        path, width=width, height=height, fps=MEASURE_FPS, start=start,
        crop=crop,
    )  # fmt: skip
    grey = luma(frames)
    moves = motion_of(grey)
    if moves:
        moves[0] = 0.0  # no frame before the window: its first change is unknown
    times = [start + i / MEASURE_FPS for i in range(len(moves))]
    return tail_reading(times, moves, end=duration, last_mark=last_mark, start=start)


def silent_opening(spine: Any, episode: int) -> bool:
    """Whether the episode opens silent / visual: its first beat has no spoken line.

    Parameters
    ----------
    spine
        The saved spine (``None``: not known, so not silent).
    episode
        Episode ordinal.

    Returns
    -------
    bool
        True when the opening beat carries no dialogue line (no face check then).
    """

    from creation.spine_view import episode_id_for, spoken_lines

    if not isinstance(spine, dict):
        return False
    episode_id = episode_id_for(spine, episode)
    beats = [
        b
        for b in spine.get("beats") or []
        if isinstance(b, dict) and b.get("episode_id") == episode_id
    ]
    if not beats:
        return False
    first = min(beats, key=lambda b: int(b.get("ordinal") or 0))
    return not any((line.original or "").strip() for line in spoken_lines(spine, first))


def first_shot_face(facts: Any) -> float | None:
    """The take facts' head-count face score for the first shot (``None`` when the facts give no shots).

    Parameters
    ----------
    facts
        Saved take facts (``{"take_facts": {...}}`` or the facts alone).

    Returns
    -------
    float | None
        :data:`creation.post.faces.HEAD_COUNT_FACE` when a named character is
        in the first shot, 0 when none is, ``None`` when unknown.
    """

    from creation.post.faces import HEAD_COUNT_FACE

    if not isinstance(facts, dict):
        return None
    inner = facts.get("take_facts", facts)
    shots = [s for s in inner.get("shots") or [] if isinstance(s, dict)]
    if not shots:
        return None
    first = min(shots, key=lambda s: float(s.get("start_seconds") or 0.0))
    people = first.get("people")
    if not isinstance(people, dict):
        return None
    return HEAD_COUNT_FACE if people.get("named") else 0.0


def opening_context(
    desk: Path, episode: int, take_id: str = "t1"
) -> tuple[bool, float | None]:
    """``(silent_open, head_count_face)`` for an episode's opening, from the saved spine and take facts.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal (any episode, not only the first).
    take_id
        The take that opens it.

    Returns
    -------
    tuple[bool, float | None]
        Whether the opening is silent / visual, and the first shot's head-count face score.
    """

    import json

    from creation.post.desk import saved_spine
    from creation.post.sfx import saved_take_facts

    found = saved_spine(desk, episode)
    silent = silent_opening(found[0] if found else None, episode)
    facts_path = saved_take_facts(desk, episode, take_id)
    facts = None
    if facts_path is not None:
        try:
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            facts = None
    return silent, first_shot_face(facts)


__all__ = [
    "DARK_LUMA",
    "OPENING_SECONDS",
    "STATIC_MOTION",
    "TAIL_MAX_SECONDS",
    "OpeningReading",
    "TailReading",
    "luma",
    "measure_opening",
    "measure_tail",
    "motion_of",
    "silent_opening",
    "first_shot_face",
    "opening_context",
    "opening_reading",
    "opening_warnings",
    "tail_reading",
    "tail_warning",
]
