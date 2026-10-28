"""On a letterbox file, nothing in the top band touches the Sokii mark.

Founder, 28 Oct 2026 (with the portrait hook-line fix): "ensure that this does
not become an issue with letterbox". The letterbox title block (setup line
white, hook line yellow, up to two lines each) sits in the black band above
the 4:3 picture. The block may not rise above the title box, whose top is the
mark's row plus ``MARK_HEIGHT`` (63) plus a 20 px gap: y 256 on 1080x1920.
The header-logo mark (27 Oct 2026) is drawn 74 px tall, its box y 173-247 and
its ink y 183-233. So the block clears the mark's box by 9 px even though
``MARK_HEIGHT`` predates the taller mark. These checks measure the mark's box
from :mod:`creation.post.watermark` (its own image size and width share), not
from ``MARK_HEIGHT``. If the mark grows or the gap shrinks, they fail.

The block must also end above the picture (y 555 on 1920), with the server's
lower title anchor (fictora-drama ``letterbox_part_titles``, 512) as well.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from creation.post.delivery_geometry import ink_offset, layout
from creation.post.letterbox import (
    TitleBlock,
    _block_height,
    fit_title,
    title_ass,
    under_server_anchor,
)
from creation.post.watermark import mark_size

SIZES = [(1080, 1920), (768, 1344)]
#: The longest a block gets: each part on two full lines (and one too long even at the floor size).
BLOCKS = [
    TitleBlock("Midnight Ramen Debt", "One clean minute could save him"),
    TitleBlock(
        "The Midnight Ramen Debt Collectors of the Old Harbour Market",
        "One clean minute could save him from the whole family tonight",
    ),
    TitleBlock(
        "The Midnight Ramen Debt Collectors of the Old Harbour Market and the Night Bus",
        "One clean minute could save him from the whole family tonight, if only she would stop talking",
    ),
]
#: The server's anchors for the title's bottom on 1920: none (y 435), and part titles (512).
ANCHORS = [None, 512]


def _mark_box(width: int, height: int) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` of the mark on a letterbox canvas, from watermark's own numbers."""

    x, y = layout(width, height).mark
    w, h = mark_size(width)
    return x, y, x + w, y + h


@pytest.mark.parametrize(("width", "height"), SIZES)
@pytest.mark.parametrize("block", BLOCKS, ids=["short", "four-lines", "too-long"])
@pytest.mark.parametrize("anchor", ANCHORS, ids=["anchor-435", "anchor-512"])
def test_the_letterbox_title_block_never_touches_the_mark_and_ends_above_the_picture(
    width: int, height: int, block: TitleBlock, anchor: int | None
) -> None:
    place = layout(width, height)
    if anchor is not None:
        anchor = round(anchor * height / 1920)
    place = under_server_anchor(place, anchor, height)
    fitted = fit_title(block, place)
    lines = len(fitted.setup) + len(fitted.hook)
    left, _, right, mark_bottom = _mark_box(width, height)
    # The first line's cap top; the line box itself (its ascent above the cap) is empty space.
    ink_top = place.title.bottom - _block_height(place, fitted.size, lines)
    ink_bottom = place.title.bottom + place.line_descent(fitted.size)

    assert 1 <= lines <= 4
    # The title lines run across the canvas centre and may be 90% of its width, so they share
    # the mark's columns: only rows keep them apart.
    assert place.title.x < right and place.title.right > left
    assert ink_top > mark_bottom, (
        f"title ink from y {ink_top:.1f}, mark box down to {mark_bottom} at {width}x{height}"
    )
    assert place.title.y > mark_bottom
    assert ink_bottom < place.picture.y, "the block ends above the picture"
    assert ink_offset(fitted.size) > 0


def _libass_ffmpeg() -> str | None:
    from creation.captions import find_ffmpeg

    try:
        return find_ffmpeg()[0]
    except RuntimeError:
        return None


@pytest.mark.skipif(_libass_ffmpeg() is None, reason="no ffmpeg with libass")
@pytest.mark.parametrize(("width", "height"), SIZES)
@pytest.mark.parametrize("anchor", ANCHORS, ids=["anchor-435", "anchor-512"])
def test_the_rendered_letterbox_title_ink_sits_between_the_mark_and_the_picture(
    tmp_path: Path, width: int, height: int, anchor: int | None
) -> None:
    from creation.captions import burn_ass

    ffmpeg = _libass_ffmpeg()
    assert ffmpeg
    black = tmp_path / "black.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=2:r=24",
         "-pix_fmt", "yuv420p", str(black)],
        check=True,
    )  # fmt: skip
    ass = tmp_path / "title.ass"
    bottom = None if anchor is None else round(anchor * height / 1920)
    text, fitted = title_ass(
        BLOCKS[1], width=width, height=height, duration=2.0, title_bottom=bottom
    )
    assert len(fitted.setup) + len(fitted.hook) == 4, "the tallest block: four lines"
    ass.write_text(text, encoding="utf-8")
    out = tmp_path / "title.mp4"
    burn_ass(ffmpeg, black, ass, out)
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    frame = np.frombuffer(raw, np.uint8).reshape(height, width)
    rows = np.flatnonzero((frame > 60).any(axis=1))

    assert rows.size, "the title was drawn"
    assert rows[0] > _mark_box(width, height)[3], (
        f"title ink from row {rows[0]}, mark box down to {_mark_box(width, height)[3]}"
    )
    assert rows[-1] < layout(width, height).picture.y
