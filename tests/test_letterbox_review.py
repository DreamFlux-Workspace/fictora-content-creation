"""``review`` on a letterbox file measures the picture, not the black bands (6 Oct 2026).

A letterbox show's 9:16 file is mostly black: the 4:3 picture sits at y
555-1365 with the title above it and the captions under it. Measured on the
whole frame, the bands read as picture: a bright opening reads dark, a moving
picture reads frozen, the band captions read as cuts, and the board's PSNR
compares the canvas with a 4:3 board. On a letterbox file (its finish record or
reel plan says so) every picture check reads only the picture, so its numbers
match the 4:3 take's own. A portrait take is untouched
(``tests/test_review_portrait_unchanged.py``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg

from creation.post.finish_record import write_finish_record
from creation.post.letterbox import pad_to_canvas
from creation.post.review import review_take

pytestmark = needs_ffmpeg


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _section(review, name: str):  # type: ignore[no-untyped-def]
    return next(s for s in review.sections if s.name == name)


@pytest.fixture
def letterbox_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A 4:3 take that moves gently, opens on a mid-grey picture and has one hard cut, and its 9:16 file."""

    desk = tmp_path / "desk"
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    (desk / "series.json").write_text("{}", encoding="utf-8")
    (desk / "ep01" / "api").mkdir()
    (desk / "ep01" / "api" / "spine.json").write_text(
        json.dumps({"title": "Three Payments Late", "delivery_format": "letterbox"}),
        encoding="utf-8",
    )
    # 3 s of a slowly drifting grey gradient (luma ~0.35), a hard cut to a brighter one for 3 s.
    pieces = [
        "gradients=s=192x144:c0=0x404040:c1=0x808080:speed=0.02:d=3:r=24",
        "gradients=s=192x144:c0=0xa0a0a0:c1=0xe0e0e0:speed=0.02:d=3:r=24",
    ]
    inputs = [x for p in pieces for x in ("-f", "lavfi", "-i", p)]
    inputs += ["-f", "lavfi", "-i", "sine=f=440:d=6:sample_rate=48000"]
    take = takes / "take-ep01-t1-mix-v1.mp4"
    _run([*inputs, "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]", "-map", "[v]",
          "-map", "2:a", "-af", "volume=0.2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(take)])  # fmt: skip
    canvas = pad_to_canvas(take, takes / "take-ep01-t1-letterbox-v1.mp4")
    write_finish_record(
        desk, episode=1, take_id="t1", complete=True, pre_bed=take, master=canvas,
        final=canvas, bed=None, bed_db=-16.5, duck_db=None,
        letterbox={"letterbox": True, "caption_colour": "yellow"},
    )  # fmt: skip
    return desk, take, canvas


def test_review_reads_only_the_picture_on_a_letterbox_file(
    letterbox_files: tuple[Path, Path, Path],
) -> None:
    desk, take, canvas = letterbox_files

    on_take = review_take(desk, take_file=take, face_detector=None, text_ocr=_no_text)
    on_canvas = review_take(
        desk, take_file=canvas, face_detector=None, text_ocr=_no_text
    )

    # Opening: frame 0's brightness is the picture's, not diluted by the black bands.
    take_open = _section(on_take, "Opening").data["opening"]["frame0_luma"]
    canvas_open = _section(on_canvas, "Opening").data["opening"]["frame0_luma"]
    assert canvas_open == pytest.approx(take_open, abs=0.03)
    # Frames: the picture's own movement (the bands would pull it toward zero, i.e. "frozen").
    take_move = _section(on_take, "Frames").data["lowest_frame_rmse"]
    canvas_move = _section(on_canvas, "Frames").data["lowest_frame_rmse"]
    assert canvas_move == pytest.approx(take_move, rel=0.35, abs=0.0005)
    assert _section(on_canvas, "Frames").data.get("region") == [0, 555, 1080, 810]
    # Cuts: the same hard cut.
    assert _section(on_canvas, "Cuts").data["cuts"] == pytest.approx(
        _section(on_take, "Cuts").data["cuts"], abs=0.05
    )
    assert any("picture only" in d for d in _section(on_canvas, "Frames").details)


def _no_text(frames: object, languages: object) -> str:
    return ""
