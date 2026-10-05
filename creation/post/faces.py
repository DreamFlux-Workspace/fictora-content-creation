"""A face / close-up signal for picture checks: a small local detector, or the take facts' head count.

The reel's cold-open score and the opening checks ask one question of a frame:
is a face there, and how close is it? Two sources answer it, best first:

1. **A local detector** (``source = "detector"``): OpenCV's frontal-face Haar
   cascade, when ``opencv-python-headless`` (4.x) is installed
   (``uv sync --extra faces``). Free, on this laptop, no download beyond the
   wheel (the cascade ships inside it). A frame's reading is the face count and
   the largest face's share of the frame; :func:`closeup_score` turns the share
   into 0-1 (a face filling :data:`CLOSEUP_SHARE` of the frame or more is 1).
2. **The take facts' head count** (``source = "head_count"``): without the
   detector, a shot the take facts put a named character in reads as
   :data:`HEAD_COUNT_FACE` (a face is probably there, closeness unknown), any
   other shot as 0.

Haar cascades find real, frontal faces; a stylised or profile face may read
as none. Every use of this signal is a weight or a warning, never a stop.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

#: A face filling this share of the frame (or more) is a full close-up (score 1).
CLOSEUP_SHARE = 0.06
#: A named character in the shot, with no detector: a face is probably there, closeness unknown.
HEAD_COUNT_FACE = 0.6
#: The smallest face the detector looks for, as a share of the frame's shorter side.
MIN_FACE_SIDE = 0.08
#: The frame size faces are looked for at (width, height): 9:16, big enough for a mid shot's face.
FACE_SIZE = (270, 480)
#: Faces are looked for this many times a second (between samples the nearest reading holds).
FACE_FPS = 4.0


@dataclass(frozen=True)
class FaceReading:
    """Faces found on one frame.

    Parameters
    ----------
    count
        Faces found.
    largest_share
        The largest face's area as a share of the frame (0 when none).
    """

    count: int
    largest_share: float

    @property
    def score(self) -> float:
        """0-1: no face 0, a full close-up 1 (:func:`closeup_score`)."""

        return closeup_score(self.largest_share) if self.count else 0.0


#: ``frame (H, W, 3) uint8 RGB -> FaceReading``.
Detector = Callable[[npt.NDArray[Any]], FaceReading]


def closeup_score(share: float) -> float:
    """A face's share of the frame as 0-1 closeness (square root: a mid shot still counts).

    Parameters
    ----------
    share
        The face's area over the frame's.

    Returns
    -------
    float
        ``min(1, sqrt(share / CLOSEUP_SHARE))``.
    """

    if share <= 0:
        return 0.0
    return min(1.0, math.sqrt(share / CLOSEUP_SHARE))


@lru_cache(maxsize=1)
def _cascade() -> Any:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError:
        return None
    classifier = getattr(cv2, "CascadeClassifier", None)
    data = getattr(getattr(cv2, "data", None), "haarcascades", None)
    if classifier is None or not data:
        # OpenCV 5 moved the cascades out of the main wheel: no detector.
        return None
    cascade = classifier(str(data) + "haarcascade_frontalface_default.xml")
    return None if cascade.empty() else cascade


def local_detector() -> Detector | None:
    """The OpenCV Haar frontal-face detector, or ``None`` when OpenCV 4.x is not installed.

    Returns
    -------
    Detector | None
        A callable reading one RGB frame.
    """

    cascade = _cascade()
    if cascade is None:
        return None
    import cv2  # type: ignore[import-not-found]

    def detect(frame: npt.NDArray[Any]) -> FaceReading:
        image = np.asarray(frame)
        if image.dtype != np.uint8:
            image = np.clip(image * (255.0 if image.max() <= 1.0 else 1.0), 0, 255)
            image = image.astype(np.uint8)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
        gray = cv2.equalizeHist(gray)
        side = max(12, int(MIN_FACE_SIDE * min(gray.shape[:2])))
        found = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(side, side)
        )
        boxes = [tuple(int(v) for v in box) for box in (found if len(found) else [])]
        if not boxes:
            return FaceReading(0, 0.0)
        area = float(gray.shape[0] * gray.shape[1])
        return FaceReading(len(boxes), max(w * h for _, _, w, h in boxes) / area)

    return detect


def detect_faces(
    frames: npt.NDArray[Any], detector: Detector | None
) -> list[FaceReading] | None:
    """Read every frame of ``frames`` with ``detector`` (``None`` when there is no detector).

    Parameters
    ----------
    frames
        ``(N, H, W, 3)`` RGB frames (uint8 or 0-1 floats).
    detector
        From :func:`local_detector` (or a test's stand-in).

    Returns
    -------
    list[FaceReading] | None
        One reading per frame.
    """

    if detector is None:
        return None
    return [detector(frame) for frame in frames]


def face_track(
    path: Path,
    detector: Detector | None,
    *,
    fps: float = FACE_FPS,
    max_seconds: float | None = None,
) -> list[tuple[float, FaceReading]] | None:
    """Faces through a video, ``fps`` times a second, streamed (a take never sits in memory).

    Parameters
    ----------
    path
        Video file.
    detector
        From :func:`local_detector`; ``None`` returns ``None`` (nothing decoded).
    fps
        Readings a second.
    max_seconds
        Stop after this many seconds (``None``: the whole file).

    Returns
    -------
    list[tuple[float, FaceReading]] | None
        ``(seconds, reading)`` per frame read.
    """

    from creation.post.media import iter_frames

    if detector is None:
        return None
    width, height = FACE_SIZE
    found: list[tuple[float, FaceReading]] = []
    for index, frame in enumerate(
        iter_frames(path, width=width, height=height, fps=fps)
    ):
        at = index / fps
        if max_seconds is not None and at > max_seconds + 1e-6:
            break
        found.append((round(at, 4), detector(frame)))
    return found


def nearest_scores(
    track: Sequence[tuple[float, FaceReading]], times: Sequence[float]
) -> tuple[float, ...]:
    """Each of ``times`` given the close-up score of the nearest reading in ``track``.

    Parameters
    ----------
    track
        From :func:`face_track`.
    times
        Sample times.

    Returns
    -------
    tuple[float, ...]
        One 0-1 score per time (empty when ``track`` is).
    """

    if not track:
        return ()
    at = np.asarray([t for t, _ in track])
    scores = [reading.score for _, reading in track]
    return tuple(scores[int(np.abs(at - t).argmin())] for t in times)


__all__ = [
    "CLOSEUP_SHARE",
    "FACE_FPS",
    "FACE_SIZE",
    "HEAD_COUNT_FACE",
    "Detector",
    "FaceReading",
    "closeup_score",
    "detect_faces",
    "face_track",
    "local_detector",
    "nearest_scores",
]
