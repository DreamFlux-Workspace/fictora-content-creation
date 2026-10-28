"""On a letterbox file, the title block never touches the Sokii mark, wherever the server puts it.

Founder, 28 Oct 2026 (with the portrait hook-line fix): "ensure that this does
not become an issue with letterbox". The letterbox title block (setup line
white, hook line yellow, up to two lines each) sits in the black band above
the 4:3 picture, and it does not move: its box top is y 256 on 1080x1920 and
its last baseline y 435 (or the server's part-title anchor, 512).

Since fictora-drama #1133 the server puts the mark inside the picture, top
left, and the kit draws exactly that place and size. An older server still
answers the band place (the portrait copy's corner, no size: the kit's size). The title must clear the mark in both: under it
in the band (by 16 px), and above the picture (so above a mark inside it).
The block must also end above the picture with the longest allowed lines.
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
from creation.post import delivery_geometry as dg
from creation.post.watermark import LetterboxPlace, mark_size

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


def _mark_boxes(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """``(left, top, right, bottom)`` of the mark: the kit's fallback (inside the picture), the new
    server's (inside the picture, its own size) and an older server's (the band, the kit's size)."""

    def box(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
        return x, y, x + w, y + h

    kit_w, kit_h = mark_size(width)
    inside = layout(width, height).mark
    fallback = box(*inside, kit_w, kit_h)
    # The server's own size can differ from the kit's by its rounding: a pixel or two either way.
    server = box(inside[0], inside[1], kit_w + 2, kit_h + 2)
    # An older server (before fictora-drama #1133) answers the band place: the band's old mark row.
    older = box(
        round(width * 0.03), round(dg.MARK_Y * height / dg.CANVAS_HEIGHT), kit_w, kit_h
    )
    return [fallback, server, older]


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
    # The first line's cap top; the line box itself (its ascent above the cap) is empty space.
    ink_top = place.title.bottom - _block_height(place, fitted.size, lines)
    ink_bottom = place.title.bottom + place.line_descent(fitted.size)

    assert 1 <= lines <= 4
    assert ink_bottom < place.picture.y, "the block ends above the picture"
    assert ink_offset(fitted.size) > 0
    for left, top, right, bottom in _mark_boxes(width, height):
        # The title lines run across the canvas centre and may be 90% of its width, so they share
        # the mark's columns: only rows keep them apart.
        assert place.title.x < right and place.title.right > left
        assert ink_top > bottom or ink_bottom < top, (
            f"title ink y {ink_top:.1f}-{ink_bottom:.1f} on the mark (y {top}-{bottom}) at {width}x{height}"
        )


def test_the_letterbox_mark_is_drawn_at_the_servers_place_and_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    import creation.post.letterbox as lb
    import creation.post.watermark as wm

    ran: list[list[str]] = []
    monkeypatch.setattr(
        lb,
        "probe_video",
        lambda _v: SimpleNamespace(width=1080, height=1920, has_audio=False, duration_seconds=2.0),
    )  # fmt: skip
    monkeypatch.setattr(lb, "run_ffmpeg", lambda args: ran.append(args))

    def graph() -> str:
        return ran[-1][ran[-1].index("-filter_complex") + 1]

    monkeypatch.setattr(
        wm, "server_letterbox_place", lambda *a, **k: LetterboxPlace(41, 590, None, None, 199, 64)
    )  # fmt: skip
    lb.mark_and_title(tmp_path / "in.mp4", tmp_path / "a.mp4", title=None)
    assert "scale=199:64:flags=lanczos" in graph() and "overlay=41:590" in graph()
    # An older server (no size): its place, the kit's size.
    monkeypatch.setattr(
        wm, "server_letterbox_place", lambda *a, **k: LetterboxPlace(32, 173)
    )
    lb.mark_and_title(tmp_path / "in.mp4", tmp_path / "b.mp4", title=None)
    kit_w = mark_size(1080)[0]
    assert f"scale={kit_w}:-1:flags=lanczos" in graph() and "overlay=32:173" in graph()
    # No server: the kit's place inside the picture.
    monkeypatch.setattr(wm, "server_letterbox_place", lambda *a, **k: None)
    lb.mark_and_title(tmp_path / "in.mp4", tmp_path / "c.mp4", title=None)
    x, y = layout().mark
    assert f"overlay={x}:{y}" in graph() and y > layout().picture.y


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
    for _left, top, _right, bottom in _mark_boxes(width, height):
        assert rows[0] > bottom or rows[-1] < top, (
            f"title ink rows {rows[0]}-{rows[-1]} on the mark (y {top}-{bottom})"
        )
    assert rows[-1] < layout(width, height).picture.y
