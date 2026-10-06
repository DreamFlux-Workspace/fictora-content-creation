"""Face detectors: both cascades unioned, the anime one first on an anime show, a loud fallback,
and the band check the hook-line overlay calls."""

from __future__ import annotations

import builtins
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg, shot_frames, write_frames

from creation.post import faces
from creation.post.faces import (
    BandFaces,
    FaceReading,
    anime_style,
    union_boxes,
    upper_band_face,
)


def test_union_keeps_both_detectors_faces_and_counts_a_shared_face_once() -> None:
    human = [(10, 10, 40, 40)]
    anime = [(12, 12, 40, 40), (150, 300, 60, 60)]  # the first is the same face
    assert union_boxes(human, anime) == [(10, 10, 40, 40), (150, 300, 60, 60)]


def test_the_priority_detector_wins_a_shared_face() -> None:
    human = [(10, 10, 40, 40)]
    anime = [(12, 12, 44, 44)]
    assert union_boxes(anime, human) == [(12, 12, 44, 44)]
    assert union_boxes(human, anime) == [(10, 10, 40, 40)]


def test_union_drops_a_detectors_own_duplicates() -> None:
    assert union_boxes([(0, 0, 50, 50), (2, 2, 50, 50)], []) == [(0, 0, 50, 50)]


class _FakeCascade:
    def __init__(
        self,
        found: list[tuple[int, int, int, int]],
        calls: list[tuple[str, int]],
        name: str,
    ):
        self.found, self.calls, self.name = found, calls, name

    def detectMultiScale(self, gray, scaleFactor, minNeighbors, minSize):  # noqa: N802, N803
        self.calls.append((self.name, minNeighbors))
        return np.asarray(self.found)


def _stub_cascades(
    monkeypatch: pytest.MonkeyPatch, human, anime
) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    table = {
        "human": _FakeCascade(human, calls, "human"),
        "anime": _FakeCascade(anime, calls, "anime"),
    }
    # No profile cascade here (``None``: it finds nothing): these tests are about the front-on pair.
    monkeypatch.setattr(faces, "_cascade", lambda kind: table.get(kind))
    return calls


class _MirrorAwareCascade(_FakeCascade):
    """A profile cascade that sees a face only on the mirrored frame (a face turned the other way)."""

    def detectMultiScale(self, gray, scaleFactor, minNeighbors, minSize):  # noqa: N802, N803
        mirrored = bool(gray[0, 0] == 255)
        self.calls.append((self.name + ("-mirror" if mirrored else ""), minNeighbors))
        return np.asarray(self.found if mirrored else [])


def test_the_profile_cascade_reads_both_ways_and_only_adds_faces_the_others_missed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    table = {
        "human": _FakeCascade([], calls, "human"),
        "anime": _FakeCascade([(200, 300, 40, 40)], calls, "anime"),
        "profile": _MirrorAwareCascade(
            [(20, 30, 60, 60), (30, 300, 40, 40)], calls, "profile"
        ),
    }
    monkeypatch.setattr(faces, "_cascade", lambda kind: table.get(kind))
    detector = faces.local_detector(anime=True)
    assert detector is not None
    frame = np.zeros((480, 270, 3), dtype=np.uint8)
    frame[0, -1] = 255  # the mirror puts this pixel at [0, 0]
    reading = detector(frame)
    # Mirrored back: x = 270 - 20 - 60 = 190. The second profile box is the anime face, mirrored: dropped.
    assert reading.boxes == ((200, 300, 40, 40), (190, 30, 60, 60))
    assert ("profile", faces.PROFILE_NEIGHBOURS) in calls
    assert ("profile-mirror", faces.PROFILE_NEIGHBOURS) in calls


def test_the_detector_runs_both_cascades_and_unions_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_cascades(
        monkeypatch, human=[(10, 10, 40, 40)], anime=[(100, 200, 80, 80)]
    )
    detector = faces.local_detector()
    assert detector is not None
    reading = detector(np.zeros((480, 270, 3), dtype=np.uint8))
    assert reading.count == 2
    assert set(reading.boxes) == {(10, 10, 40, 40), (100, 200, 80, 80)}
    assert reading.largest_share == pytest.approx(80 * 80 / (270 * 480))
    assert sorted(calls) == [("anime", 5), ("human", 5)]


def test_an_anime_show_puts_the_anime_cascade_first_and_the_real_face_one_stricter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_cascades(
        monkeypatch, human=[(10, 10, 40, 40)], anime=[(12, 12, 44, 44)]
    )
    detector = faces.local_detector(anime=True)
    assert detector is not None
    reading = detector(np.zeros((480, 270, 3), dtype=np.uint8))
    assert reading.boxes == ((12, 12, 44, 44),), (
        "the anime box stands for the shared face"
    )
    assert ("human", faces.STRICT_NEIGHBOURS) in calls and (
        "anime",
        faces.NEIGHBOURS,
    ) in calls


@pytest.mark.parametrize(
    ("styles", "anime"),
    [
        (({"art_style_preset_id": "builtin-serialized-anime"},), True),
        (("webtoon_ink",), True),
        (({"art_style": "Korean manhwa, clean lines"},), True),
        (("slice-of-life",), False),
        (("excellent parcel cello",), False),
        ((None, ""), None),
    ],
)
def test_anime_style(styles: tuple, anime: bool | None) -> None:
    assert anime_style(*styles) is anime


def test_a_missing_opencv_is_said_loudly_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def no_cv2(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("No module named 'cv2'")
        return real_import(name, *args, **kwargs)

    faces._cv2.cache_clear()
    faces._cascade.cache_clear()
    monkeypatch.setattr(builtins, "__import__", no_cv2)
    try:
        assert faces.local_detector() is None
        assert "!! face detector unavailable" in capsys.readouterr().err
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        faces._cv2.cache_clear()
        faces._cascade.cache_clear()


def _detector_at(box_fraction: tuple[float, float, float, float]):
    def detect(frame: np.ndarray) -> FaceReading:
        h, w = frame.shape[:2]
        x, y, bw, bh = box_fraction
        box = (int(x * w), int(y * h), int(bw * w), int(bh * h))
        return FaceReading(1, bw * bh, (box,), (w, h))

    return detect


def test_upper_band_face_on_a_frame() -> None:
    frame = np.zeros((480, 270, 3), dtype=np.uint8)
    high = upper_band_face(
        frame, top=0.08, bottom=0.55, detector=_detector_at((0.3, 0.1, 0.3, 0.2))
    )
    assert isinstance(high, BandFaces) and bool(high) is True
    assert high.boxes[0][1] == pytest.approx((0.3, 0.1, 0.3, 0.2), abs=0.01)
    low = upper_band_face(
        frame, top=0.08, bottom=0.55, detector=_detector_at((0.3, 0.7, 0.3, 0.2))
    )
    assert low is not None and bool(low) is False and len(low.boxes) == 1
    assert upper_band_face(frame, top=0.08, bottom=0.55, detector=None) is None
    with pytest.raises(ValueError):
        upper_band_face(frame, top=0.6, bottom=0.5, detector=None)


@needs_ffmpeg
def test_upper_band_face_on_a_video_matches_the_hook_overlay_call(
    tmp_path: Path,
) -> None:
    """``probe(video, start_s, end_s, top=TOP_STRIP, bottom=CAPTION_BAND[0])``, as PR #122 calls it."""

    clip = write_frames(tmp_path / "t.mp4", shot_frames(72, (200, 180, 150)))
    seen: list[float] = []

    def detect(frame: np.ndarray) -> FaceReading:
        seen.append(1.0)
        return _detector_at((0.3, 0.1, 0.3, 0.2))(frame)

    answer = upper_band_face(clip, 0.0, 2.0, top=0.08, bottom=0.55, detector=detect)
    assert answer and len(seen) == 9  # 0.0-2.0 s at 4 a second
    assert answer.boxes[-1][0] == pytest.approx(2.0)
