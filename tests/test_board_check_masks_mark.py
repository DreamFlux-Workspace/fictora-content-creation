"""The review's Board check leaves the Sokii mark out of the comparison (25 Oct 2026).

A finished portrait file carries the mark top left (3% in, 9% down, the
approved reels' size); no storyboard has it. The check compares every frame
with the board, so the mark's box is left out of that comparison: a board frame
in a marked file then reads as the board itself, and a raw take (no mark) is
measured the same as before. A letterbox file is compared on its 4:3 picture
only, where the mark never is.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from conftest import needs_ffmpeg
from PIL import Image

from creation.post.deboard import keep_mask
from creation.post.review import mark_mask_box, scan_board
from creation.post.watermark import watermark


def test_the_mask_box_covers_the_mark_where_it_is_drawn() -> None:
    left, top, right, bottom = mark_mask_box(1080, 1920)
    # The mark on a 1080x1920 copy: (32, 173), 101x88.
    assert left * 1080 <= 32 and top * 1920 <= 173
    assert right * 1080 >= 32 + 101 and bottom * 1920 >= 173 + 88
    # And no more than a small corner of the frame.
    assert (right - left) * (bottom - top) < 0.01


def test_the_keep_mask_drops_only_the_box() -> None:
    keep = keep_mask(192, 336, (0.0, 0.0, 0.5, 0.25))
    assert keep is not None
    assert not keep[:84, :96].any() and keep[84:, :].all() and keep[:, 96:].all()
    assert keep_mask(192, 336, None) is None


@needs_ffmpeg
def test_a_board_frame_in_a_marked_file_reads_as_the_board(tmp_path: Path) -> None:
    board = tmp_path / "board.png"
    # A smooth board (it survives encoding nearly whole), dark where the mark goes.
    ys, xs = np.mgrid[0:1344, 0:768]
    picture = np.stack(
        [xs * 255 // 767, ys * 255 // 1343, np.full_like(xs, 60)], axis=-1
    )
    Image.fromarray(picture.astype(np.uint8)).save(board)
    scene = tmp_path / "scene.mp4"
    flash = tmp_path / "flash.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=768x1344:r=24:d=3",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12", str(scene)], check=True)  # fmt: skip
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(board), "-t", "0.5", "-r", "24",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12", str(flash)], check=True)  # fmt: skip
    listing = tmp_path / "list.txt"
    listing.write_text(f"file '{scene}'\nfile '{flash}'\nfile '{scene}'\n")
    joined = tmp_path / "joined.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(joined)], check=True)  # fmt: skip
    marked = watermark(joined, tmp_path / "marked.mp4")

    masked = scan_board(marked, board, mask_box=mark_mask_box(768, 1344))
    whole = scan_board(marked, board)

    assert masked.later and whole.later
    # Left out, the mark no longer drags the board frames' match down.
    assert min(v for _i, v in masked.later) > max(v for _i, v in whole.later) + 3


class _Stop(Exception):
    pass


@needs_ffmpeg
def test_review_leaves_the_mark_out_on_a_portrait_take(
    post_desk: Path, monkeypatch
) -> None:
    from conftest import make_take

    import creation.post.review as review

    take = post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4"
    make_take(take)
    seen: dict[str, object] = {}

    def record(*_args: object, **kwargs: object) -> None:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(review, "board_section", record)
    try:
        review.review_take(post_desk, episode=1, take_id="t1")
    except _Stop:
        pass
    assert seen.get("crop") is None
    assert seen.get("mask_box") is not None
