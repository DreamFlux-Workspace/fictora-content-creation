"""``blur``: Gaussian-blur pixel boxes of a take inside a time window (real ffmpeg, tiny synthetic takes)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import needs_ffmpeg, shot_frames, write_frames

from creation.cli_produce import main
from creation.post.edit import BlurBox, blur_boxes, parse_box
from creation.post.finish_record import write_finish_record
from creation.post.lineage import raw_take_behind
from creation.post.media import count_frames, decode_frames

W, H = 96, 168
BLUE = (40, 60, 200)
#: Where the fake sign sits: x 20-60, y 40-70.
SIGN = BlurBox(20, 40, 40, 30)


def _signed_frames(count: int = 72) -> list[Any]:
    """Drifting gradient with high-contrast 2 px stripes (stand-in for garbled sign text) in :data:`SIGN`."""

    frames = shot_frames(count, BLUE)
    stripes = np.where((np.arange(SIGN.w) // 2) % 2 == 0, 0, 255).astype(np.uint8)
    for frame in frames:
        frame[SIGN.y : SIGN.y + SIGN.h, SIGN.x : SIGN.x + SIGN.w] = stripes[
            None, :, None
        ]
    return frames


def _frames(path: Path) -> np.ndarray:
    return decode_frames(path, width=W, height=H)


def _pcm(path: Path) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip


def _sign(frame: np.ndarray, inset: int = 0) -> np.ndarray:
    return frame[
        SIGN.y + inset : SIGN.y + SIGN.h - inset,
        SIGN.x + inset : SIGN.x + SIGN.w - inset,
    ]


def _text_contrast(frame: np.ndarray, inset: int = 0) -> float:
    """Mean jump between neighbouring columns inside the sign: high for readable stripes, ~0 when blurred."""

    return float(np.abs(np.diff(_sign(frame, inset).mean(axis=2), axis=1)).mean())


@needs_ffmpeg
def test_blur_hides_the_box_only_inside_the_window_and_copies_the_sound(
    tmp_path: Path,
) -> None:
    take = write_frames(tmp_path / "take.mp4", _signed_frames(), tone=440)
    out = blur_boxes(take, tmp_path / "blur.mp4", (SIGN,), start=1.0, end=2.0)

    assert out.boxes == (SIGN,) and (out.start_seconds, out.end_seconds) == (1.0, 2.0)
    assert count_frames(out.output) == count_frames(take)
    assert _pcm(out.output) == _pcm(take), "the sound is copied untouched"
    before, after = _frames(take), _frames(out.output)
    assert _text_contrast(before[30]) > 60, "(the source sign is readable)"
    for index in (26, 30, 40, 46):
        assert _text_contrast(after[index]) < 6, (
            f"frame {index} (inside 1-2 s): the sign is blurred"
        )
    for index in (0, 10, 20, 52, 60, 71):
        assert np.abs(after[index] - before[index]).mean() < 2, (
            f"frame {index} (outside the window) unchanged"
        )
    outside = np.ones((H, W), dtype=bool)
    outside[SIGN.y - 2 : SIGN.y + SIGN.h + 2, SIGN.x - 2 : SIGN.x + SIGN.w + 2] = False
    assert np.abs(after[30][outside] - before[30][outside]).mean() < 2, (
        "outside the box: unchanged"
    )


@needs_ffmpeg
def test_feather_softens_outside_the_box_and_still_hides_the_text_to_its_edge(
    tmp_path: Path,
) -> None:
    take = write_frames(tmp_path / "take.mp4", _signed_frames(), tone=440)
    hard = blur_boxes(take, tmp_path / "hard.mp4", (SIGN,), start=0.0, end=3.0)
    soft = blur_boxes(
        take, tmp_path / "soft.mp4", (SIGN,), start=0.0, end=3.0, feather=8
    )

    before, h, s = _frames(take)[30], _frames(hard.output)[30], _frames(soft.output)[30]
    assert _text_contrast(s) < 6, (
        "the whole box, edges included, is as blurred as the hard edge"
    )
    edge = (slice(SIGN.y, SIGN.y + SIGN.h), slice(SIGN.x, SIGN.x + 2))
    assert np.abs(np.diff(s[edge].mean(axis=2), axis=1)).mean() < 6, (
        "the box's own edge columns are hidden"
    )
    band = (
        slice(SIGN.y, SIGN.y + SIGN.h),
        slice(SIGN.x + SIGN.w + 1, SIGN.x + SIGN.w + 5),
    )
    assert np.abs(h[band] - before[band]).mean() < 2, (
        "(hard edge: nothing outside the box moves)"
    )
    assert np.abs(s[band] - before[band]).mean() > 3, (
        "feather: the blur fades out past the box"
    )
    far = (slice(120, H), slice(0, W))
    assert np.abs(s[far] - before[far]).mean() < 2, "far from the box: unchanged"


def test_parse_box_reads_whole_pixels_and_refuses_the_rest() -> None:
    assert parse_box(" 120, 340,260 ,90") == BlurBox(120, 340, 260, 90)
    for bad in ("1,2,3", "1.5,2,3,4", "a,b,c,d", "-1,2,3,4"):
        with pytest.raises(ValueError, match="x,y,w,h"):
            parse_box(bad)
    with pytest.raises(ValueError, match="more than 0"):
        parse_box("1,2,0,4")


@needs_ffmpeg
def test_blur_refuses_a_box_outside_the_frame_or_a_bad_window(tmp_path: Path) -> None:
    take = write_frames(tmp_path / "take.mp4", _signed_frames(48), tone=440)
    out = tmp_path / "blur.mp4"
    with pytest.raises(ValueError, match="outside the 96x168 frame"):
        blur_boxes(take, out, (BlurBox(80, 10, 20, 10),), start=0.0, end=1.0)
    with pytest.raises(ValueError, match="outside the 96x168 frame"):
        blur_boxes(take, out, (BlurBox(0, 160, 10, 10),), start=0.0, end=1.0)
    with pytest.raises(ValueError, match="end after it starts"):
        blur_boxes(take, out, (SIGN,), start=1.0, end=1.0)
    with pytest.raises(ValueError, match="past the end"):
        blur_boxes(take, out, (SIGN,), start=5.0, end=6.0)
    with pytest.raises(ValueError, match="at least one"):
        blur_boxes(take, out, (), start=0.0, end=1.0)
    assert not out.exists()


@needs_ffmpeg
def test_odd_box_edges_grow_outward_to_even_pixels(tmp_path: Path) -> None:
    take = write_frames(tmp_path / "take.mp4", _signed_frames(24), tone=440)
    out = blur_boxes(
        take, tmp_path / "blur.mp4", (BlurBox(21, 41, 38, 28),), start=0.0, end=1.0
    )
    assert out.boxes == (BlurBox(20, 40, 40, 30),)


@needs_ffmpeg
def test_blur_command_writes_the_file_the_chain_line_and_says_it_is_a_patch(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    raw = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _signed_frames(), tone=440)

    assert main(["blur", "--desk", str(post_desk), "--box", "20,40,40,30", "--box", "0,0,10,10",
                 "--from", "1", "--to", "2", "--strength", "12"]) == 0  # fmt: skip

    said = capsys.readouterr().out
    out = takes / "take-ep01-t1-blur-v1.mp4"
    assert out.is_file() and "PATCH" in said and "report it" in said, said
    chain = [
        json.loads(line)
        for line in (takes / "edit-chain.jsonl").read_text().splitlines()
    ]
    assert chain[-1] == {
        "op": "blur", "source": "ep01/takes/take-ep01-t1-raw-v1.mp4", "output": "ep01/takes/take-ep01-t1-blur-v1.mp4",
        "boxes": [[20, 40, 40, 30], [0, 0, 10, 10]], "from": 1.0, "to": 2.0, "strength": 12.0, "feather": 0,
    }  # fmt: skip
    assert _text_contrast(_frames(out)[30]) < 6
    lineage = raw_take_behind(post_desk, out)
    assert lineage.raw == raw and lineage.keeps_timeline, (
        "blur keeps the raw take's sound timeline"
    )


@needs_ffmpeg
def test_blur_on_a_finished_take_carries_the_record_and_keeps_the_sound_before_the_bed(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    pre_bed = write_frames(
        takes / "take-ep01-t1-colour-v1.mp4", _signed_frames(), tone=440
    )
    master = write_frames(takes / "take-ep01-t1-cap-v1.mp4", _signed_frames(), tone=440)
    final = write_frames(
        takes / "take-ep01-t1-sokii-v1.mp4", _signed_frames(), tone=440
    )
    write_finish_record(post_desk, episode=1, take_id="t1", complete=True, pre_bed=pre_bed, master=master,
                        final=final, bed=None, bed_db=-16.5, duck_db=None)  # fmt: skip

    assert main(["blur", "--desk", str(post_desk), "--take-file", str(final), "--box", "20,40,40,30",
                 "--from", "0.5", "--to", "2.5"]) == 0  # fmt: skip

    said = capsys.readouterr().out
    assert "the sound before the bed is unchanged" in said, said
    record = json.loads((takes / "take-ep01-t1-finish-v2.json").read_text())
    assert record["pre_bed"] == "ep01/takes/take-ep01-t1-colour-v1.mp4", (
        "the sound is untouched: same pre-bed"
    )
    assert record["master"] == "ep01/takes/take-ep01-t1-blur-master-v1.mp4"
    assert record["final"] == "ep01/takes/take-ep01-t1-blur-v1.mp4"
    assert record["edits"] == [{"op": "blur", "boxes": [[20, 40, 40, 30]], "from": 0.5, "to": 2.5,
                                "strength": 20.0, "feather": 0, "from_record": "take-ep01-t1-finish-v1.json"}]  # fmt: skip
    for name in ("master", "final"):
        assert _text_contrast(_frames(post_desk / record[name])[30]) < 6, (
            f"the {name} is blurred too"
        )
    assert _pcm(post_desk / record["final"]) == _pcm(final)


def test_blur_is_listed_as_a_picture_only_carried_edit() -> None:
    from creation.post.edit_commands import CARRIED, EDIT_COMMANDS, PICTURE_ONLY

    assert {"blur"} <= EDIT_COMMANDS & CARRIED & PICTURE_ONLY
