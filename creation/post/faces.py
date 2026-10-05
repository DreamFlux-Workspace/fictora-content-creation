"""A face / close-up signal for picture checks: two local detectors, unioned, or (only if OpenCV fails) the head count.

The reel's cold-open score, the opening checks and the hook-line overlay ask
of a frame: is a face there, how close is it, and where is it?

1. **Two local detectors** (``source = "detector"``), both OpenCV cascades,
   both free and on this laptop (``opencv-python-headless`` is a default
   dependency):

   - OpenCV's frontal-face Haar cascade (ships inside the wheel): real faces.
   - nagadomi's ``lbpcascade_animeface.xml`` (MIT, vendored in
     ``creation/post/data/``, see ``THIRD_PARTY.md``): anime / manhwa faces,
     which the Haar cascade misses. Most Fictora shows are drawn this way.

   Both run on every frame and their boxes are **unioned** (:func:`union_boxes`):
   a box the other detector already found (IoU over :data:`SAME_FACE_IOU`) is
   one face. When the show's style is known to be anime / manhwa
   (:func:`anime_style`), the anime detector has priority: its boxes are kept
   first and the Haar cascade runs stricter (:data:`STRICT_NEIGHBOURS`), since
   on drawn faces its extra hits are mostly false. A reading carries the
   count, the largest face's share of the frame, and every box;
   :func:`closeup_score` turns the share into 0-1.
2. **The take facts' head count** (``source = "head_count"``): ONLY when
   OpenCV cannot be imported (a broken install). That is said loudly on
   stderr once per run (``!! face detector unavailable``), never silently.

Every use of this signal is a weight or a warning, never a stop.
"""

from __future__ import annotations

import logging
import math
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

_LOG = logging.getLogger(__name__)

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
#: Two boxes overlapping this much (intersection over union) are the same face.
SAME_FACE_IOU = 0.3
#: Neighbours a cascade hit needs; the de-prioritised detector on a known style needs more.
NEIGHBOURS = 5
STRICT_NEIGHBOURS = 8
#: The vendored anime-face cascade.
ANIME_CASCADE = Path(__file__).with_name("data") / "lbpcascade_animeface.xml"
#: Words in a show's art style that mean drawn anime / manhwa faces.
ANIME_WORDS = (
    "anime",
    "manga",
    "manhwa",
    "manhua",
    "webtoon",
    "chibi",
    "shoujo",
    "shonen",
)

#: ``(x, y, w, h)`` in pixels of the frame read.
Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class FaceReading:
    """Faces found on one frame.

    Parameters
    ----------
    count
        Faces found.
    largest_share
        The largest face's area as a share of the frame (0 when none).
    boxes
        Every face box, ``(x, y, w, h)`` in pixels of the frame read.
    size
        That frame's ``(width, height)``.
    """

    count: int
    largest_share: float
    boxes: tuple[Box, ...] = ()
    size: tuple[int, int] = FACE_SIZE

    @property
    def score(self) -> float:
        """0-1: no face 0, a full close-up 1 (:func:`closeup_score`)."""

        return closeup_score(self.largest_share) if self.count else 0.0

    def fractions(self) -> tuple[tuple[float, float, float, float], ...]:
        """The boxes as frame fractions ``(x, y, w, h)``, 0-1."""

        width, height = self.size
        return tuple(
            (x / width, y / height, w / width, h / height) for x, y, w, h in self.boxes
        )


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


def _iou(a: Box, b: Box) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / float(aw * ah + bw * bh - inter)


def union_boxes(
    first: Sequence[Box], second: Sequence[Box], *, same: float = SAME_FACE_IOU
) -> list[Box]:
    """Both detectors' boxes as one set of faces; ``first`` wins where they find the same face.

    Parameters
    ----------
    first
        The priority detector's boxes (kept as they are, less its own duplicates).
    second
        The other detector's boxes: each is added unless it overlaps a kept box
        by more than ``same`` (intersection over union).
    same
        IoU over which two boxes are one face.

    Returns
    -------
    list[Box]
        The faces, ``first``'s boxes first.
    """

    kept: list[Box] = []
    for box in [*first, *second]:
        if all(_iou(box, other) <= same for other in kept):
            kept.append(tuple(int(v) for v in box))  # type: ignore[arg-type]
    return kept


def anime_style(*styles: Any) -> bool | None:
    """Whether a show draws anime / manhwa faces, from its art-style words; ``None`` when nothing says.

    Parameters
    ----------
    styles
        Style strings or mappings (a spine: ``art_style_preset_id``,
        ``art_style``, ``visual_style``, ``style``), e.g. the desk's preset id.

    Returns
    -------
    bool | None
        True on an anime / manhwa / manga / webtoon style word, False on a style
        that says something else, ``None`` when no style is known.
    """

    words: list[str] = []
    for style in styles:
        if isinstance(style, Mapping):
            for key in ("art_style_preset_id", "art_style", "visual_style", "style"):
                if isinstance(style.get(key), str) and style[key].strip():
                    words.append(style[key])
        elif isinstance(style, str) and style.strip():
            words.append(style)
    if not words:
        return None
    tokens = re.split(r"[^a-z0-9]+", " ".join(words).lower())
    return any(token.startswith(word) for token in tokens for word in ANIME_WORDS)


@lru_cache(maxsize=1)
def _cv2() -> Any:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        message = (
            f"!! face detector unavailable: OpenCV did not import ({exc}). Face checks fall back to the take "
            "facts' head count (no anime faces, no boxes). Reinstall: uv sync"
        )
        print(message, file=sys.stderr, flush=True)
        _LOG.error(message)
        return None
    return cv2


@lru_cache(maxsize=2)
def _cascade(kind: str) -> Any:
    cv2 = _cv2()
    if cv2 is None:
        return None
    classifier = getattr(cv2, "CascadeClassifier", None)
    if kind == "anime":
        path = str(ANIME_CASCADE)
    else:
        data = getattr(getattr(cv2, "data", None), "haarcascades", None)
        path = str(data) + "haarcascade_frontalface_default.xml" if data else ""
    if classifier is None or not path:
        message = f"!! the {kind} face cascade is missing from this OpenCV ({cv2.__version__}): pin opencv <5"
        print(message, file=sys.stderr, flush=True)
        _LOG.error(message)
        return None
    cascade = classifier(path)
    return None if cascade.empty() else cascade


def local_detector(*, anime: bool | None = None) -> Detector | None:
    """Both cascades (real and anime faces), unioned; ``None`` only when OpenCV cannot be used.

    Parameters
    ----------
    anime
        The show's style is anime / manhwa (:func:`anime_style`): the anime
        detector has priority. ``None`` (unknown) or False: the real-face
        cascade's boxes come first, both at the same strictness.

    Returns
    -------
    Detector | None
        A callable reading one RGB frame.
    """

    human, drawn = _cascade("human"), _cascade("anime")
    if human is None and drawn is None:
        return None
    cv2 = _cv2()

    def boxes(cascade: Any, gray: Any, neighbours: int) -> list[Box]:
        if cascade is None:
            return []
        side = max(12, int(MIN_FACE_SIDE * min(gray.shape[:2])))
        found = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=neighbours, minSize=(side, side)
        )
        return [tuple(int(v) for v in box) for box in (found if len(found) else [])]  # type: ignore[misc]

    def detect(frame: npt.NDArray[Any]) -> FaceReading:
        image = np.asarray(frame)
        if image.dtype != np.uint8:
            image = np.clip(image * (255.0 if image.max() <= 1.0 else 1.0), 0, 255)
            image = image.astype(np.uint8)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
        gray = cv2.equalizeHist(gray)
        if anime:
            found = union_boxes(
                boxes(drawn, gray, NEIGHBOURS), boxes(human, gray, STRICT_NEIGHBOURS)
            )
        else:
            found = union_boxes(
                boxes(human, gray, NEIGHBOURS), boxes(drawn, gray, NEIGHBOURS)
            )
        height, width = gray.shape[:2]
        if not found:
            return FaceReading(0, 0.0, (), (width, height))
        share = max(w * h for _, _, w, h in found) / float(width * height)
        return FaceReading(len(found), share, tuple(found), (width, height))

    return detect


def detector_for(desk: Path, episode: int) -> Detector | None:
    """The local detector for a desk's show, with the anime detector first when its style is anime.

    The style comes from the episode's saved spine (``art_style_preset_id`` …)
    and the desk's production preset id.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Any episode (its saved spine).

    Returns
    -------
    Detector | None
        :func:`local_detector` for that style.
    """

    from creation.post.desk import saved_spine

    found = saved_spine(desk, episode)
    preset = None
    try:
        from creation.production_state import load_production

        preset = load_production(desk).preset_id
    except (FileNotFoundError, ValueError, TypeError):
        preset = None
    return local_detector(anime=anime_style(found[0] if found else None, preset))


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
    start: float = 0.0,
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
        Stop this many seconds after ``start`` (``None``: the whole file).
    start
        Seconds into the file to start reading.

    Returns
    -------
    list[tuple[float, FaceReading]] | None
        ``(seconds into the file, reading)`` per frame read.
    """

    from creation.post.media import iter_frames

    if detector is None:
        return None
    width, height = FACE_SIZE
    found: list[tuple[float, FaceReading]] = []
    frames = iter_frames(path, width=width, height=height, fps=fps, start=start or None)
    for index, frame in enumerate(frames):
        at = index / fps
        if max_seconds is not None and at > max_seconds + 1e-6:
            break
        found.append((round(start + at, 4), detector(frame)))
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


@dataclass(frozen=True)
class BandFaces:
    """Faces against a horizontal band of the frame (truthy when one overlaps it).

    Parameters
    ----------
    overlaps
        A face box overlaps the band on some frame read.
    boxes
        Every face box read, as frame fractions ``(x, y, w, h)``, with the
        second it was read at: ``(seconds, (x, y, w, h))``.
    """

    overlaps: bool
    boxes: tuple[tuple[float, tuple[float, float, float, float]], ...] = ()

    def __bool__(self) -> bool:
        return self.overlaps


def _in_band(box: tuple[float, float, float, float], top: float, bottom: float) -> bool:
    _, y, _, h = box
    return y < bottom and top < y + h


def upper_band_face(
    source: Path | npt.NDArray[Any],
    start_s: float = 0.0,
    end_s: float | None = None,
    *,
    top: float,
    bottom: float,
    detector: Detector | None | str = "local",
    fps: float = FACE_FPS,
) -> BandFaces | None:
    """Whether a face's box overlaps the band ``top``-``bottom`` (frame fractions, 0 at the top), and the boxes.

    The hook-line overlay asks this before it draws over the upper frame
    (``upper_band_face(video, start, end, top=TOP_STRIP, bottom=CAPTION_BAND[0])``).

    Parameters
    ----------
    source
        A video file, or one RGB frame ``(H, W, 3)``.
    start_s, end_s
        On a video: the seconds read (``end_s`` ``None``: one frame at ``start_s``).
    top, bottom
        The band, as fractions of the frame's height.
    detector
        ``"local"`` (both cascades), a stand-in, or ``None``.
    fps
        Frames read a second on a video.

    Returns
    -------
    BandFaces | None
        Truthy when a face overlaps the band; ``None`` when no detector can run
        (the caller treats it as unknown).

    Raises
    ------
    ValueError
        When the band is not ``0 <= top < bottom <= 1``.
    """

    if not 0.0 <= top < bottom <= 1.0:
        raise ValueError(f"band {top:g}-{bottom:g}: needs 0 <= top < bottom <= 1")
    found = local_detector() if detector == "local" else detector
    if found is None or isinstance(found, str):
        return None
    readings: list[tuple[float, FaceReading]]
    if isinstance(source, np.ndarray):
        readings = [(float(start_s), found(source))]
    else:
        span = None if end_s is None else max(0.0, end_s - start_s)
        track = face_track(
            Path(source), found, fps=fps, start=start_s,
            max_seconds=0.0 if span is None else span,
        )  # fmt: skip
        readings = track or []
    boxes = tuple((at, box) for at, reading in readings for box in reading.fractions())
    return BandFaces(
        overlaps=any(_in_band(box, top, bottom) for _, box in boxes), boxes=boxes
    )


__all__ = [
    "ANIME_CASCADE",
    "CLOSEUP_SHARE",
    "FACE_FPS",
    "FACE_SIZE",
    "HEAD_COUNT_FACE",
    "BandFaces",
    "Detector",
    "FaceReading",
    "anime_style",
    "closeup_score",
    "detect_faces",
    "detector_for",
    "face_track",
    "local_detector",
    "nearest_scores",
    "union_boxes",
    "upper_band_face",
]
