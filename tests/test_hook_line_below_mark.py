"""A portrait episode's top hook line starts under the Sokii mark, never on it.

Founder screenshot, 28 Oct 2026: "One clean minute could save him" was drawn
right over the mark in the top-left. The line was anchored at 9.5% down (the
top UI strip plus a gap), while the mark sits at 9% and runs 74 px tall on
1080x1920 (y 173-247). The line now starts one gap under the mark's bottom,
worked out from :mod:`creation.post.watermark`, so it follows any change to the
mark. The lower place (just above the captions) does not move.

Two checks: the drawn box (anchor, outline, two lines at full size) on the two
portrait sizes we deliver, and, where ffmpeg has libass, the rendered ink.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from creation.captions import CAPTION_BAND
from creation.post import hook_overlay as hk
from creation.post.hook_overlay import HookOverlay, overlay_ass
from creation.post.watermark import (
    MARK_X_FRACTION,
    MARK_Y_FRACTION,
    mark_bottom,
    mark_size,
)

SIZES = [(1080, 1920), (768, 1344)]
#: The founder's line (two lines at 88 px on 1080), and a longer one: two lines is the tallest a top line gets.
LONG = "One clean minute could save him from the whole family"
SHORT = "One clean minute could save him"


def _anchor(ass: str) -> tuple[str, int, int]:
    match = re.search(r"\\an(\d)\\pos\((\d+),(\d+)\)", ass)
    assert match, ass
    return match.group(1), int(match.group(2)), int(match.group(3))


def _lines(ass: str) -> int:
    return ass.rstrip().rsplit("}", 1)[1].count("\\N") + 1


def _mark_box(width: int, height: int) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` of the mark at its own place."""

    w, h = mark_size(width)
    x, y = round(width * MARK_X_FRACTION), round(height * MARK_Y_FRACTION)
    return x, y, x + w, y + h


def _hook(text: str, placement: str = "top") -> HookOverlay:
    return HookOverlay("hook", text, placement, 0.0, 3.0)  # type: ignore[arg-type]


@pytest.mark.parametrize(("width", "height"), SIZES)
@pytest.mark.parametrize("text", [SHORT, LONG])
def test_the_top_hook_lines_box_never_touches_the_mark(
    width: int, height: int, text: str
) -> None:
    ass = overlay_ass(_hook(text), width=width, height=height)
    anchor, _, y = _anchor(ass)
    outline = hk._scaled(hk.HOOK_OUTLINE, height)
    shadow = hk._scaled(hk.HOOK_SHADOW, height)
    size = hk._scaled(hk.HOOK_FONT_SIZE, height)
    left, top, right, bottom = _mark_box(width, height)

    assert anchor == "8"
    assert mark_bottom(width, height) == bottom
    # The line's box spans the frame's width between the margins, so it overlaps the mark
    # sideways; it must clear it from above: its top edge, outline included, under the mark.
    assert y - outline > bottom, (
        f"hook box top {y - outline} on the mark (y {top}-{bottom}) at {width}x{height}"
    )
    assert y - bottom >= round(hk.BAND_GAP * height), "one gap under the mark"
    # Two full-size lines (libass line height is under 1.25x the size) stay well above the captions.
    lines = _lines(ass)
    box_bottom = y + lines * size * 1.25 + outline + shadow
    assert box_bottom < CAPTION_BAND[0] * height
    assert lines <= 2


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_letterbox_title_bar_on_a_portrait_take_starts_under_the_mark(
    width: int, height: int
) -> None:
    """A letterbox show's portrait take gets the title bar; the mark sat on its corner and ran
    into a long series title (checked 28 Oct 2026). The bar now starts under the mark."""

    bar = HookOverlay(
        "title_bar", SHORT, "bar", 0.0, None, title="The Midnight Ramen Debt Collectors"
    )
    ass = overlay_ass(bar, width=width, height=height, duration=2.0)
    band_top = int(re.search(r"\\an7\\pos\(0,(\d+)\)", ass).group(1))

    assert band_top > mark_bottom(width, height)
    assert band_top + round(hk.BAR_HEIGHT * height) < CAPTION_BAND[0] * height


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_lower_place_does_not_move(width: int, height: int) -> None:
    anchor, _, y = _anchor(
        overlay_ass(_hook(LONG, "lower"), width=width, height=height)
    )

    assert anchor == "2"
    assert y == round((CAPTION_BAND[0] - hk.BAND_GAP) * height)


def test_the_face_check_band_starts_where_the_line_does() -> None:
    assert hk.HOOK_TOP * 1920 == hk.hook_top(1080, 1920) == 276
    assert hk.HOOK_TOP * 1920 > mark_bottom(1080, 1920) == 247


def _libass_ffmpeg() -> str | None:
    from creation.captions import find_ffmpeg

    try:
        return find_ffmpeg()[0]
    except RuntimeError:
        return None


@pytest.mark.skipif(_libass_ffmpeg() is None, reason="no ffmpeg with libass")
@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_rendered_top_hook_ink_starts_under_the_mark(
    tmp_path: Path, width: int, height: int
) -> None:
    from creation.captions import burn_ass

    ffmpeg = _libass_ffmpeg()
    assert ffmpeg
    grey = tmp_path / "grey.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}:d=2:r=24",
         "-pix_fmt", "yuv420p", str(grey)],
        check=True,
    )  # fmt: skip
    ass = tmp_path / "hook.ass"
    ass.write_text(
        overlay_ass(_hook(LONG), width=width, height=height), encoding="utf-8"
    )
    out = tmp_path / "hook.mp4"
    burn_ass(ffmpeg, grey, ass, out)
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    frame = np.frombuffer(raw, np.uint8).reshape(height, width).astype(int)
    ink_rows = np.flatnonzero((np.abs(frame - 128) > 40).any(axis=1))

    assert ink_rows.size, "the hook line was drawn"
    assert ink_rows[0] > mark_bottom(width, height), (
        f"ink from row {ink_rows[0]}, mark down to {mark_bottom(width, height)}"
    )
    assert ink_rows[-1] < CAPTION_BAND[0] * height
