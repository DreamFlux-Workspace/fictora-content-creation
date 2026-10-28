"""The letterbox file's ink lands where "Not Home" has it (user decision 2026-10-06), within 4 px.

Rendered with the kit's own steps (canvas, band captions, mark and title) on a
black picture and measured the way the reference was: pixels brighter than
150 (luma) outside the picture (y 555-1365), on the 1080x1920 file.

Reference (``Not Home Episode 1.mp4``, full resolution): mark ink x 39-96,
y 179-227; title line 1 (white, with a descender) rows 333-384, line 2
(yellow, no descender) rows 396-435; caption "Don't open it." rows 1417-1468,
"I've been in here" rows 1417-1457. fictora-drama's render (#614) measures
the same boxes within 4 px, so the app's file and the kit's agree.

Skipped where libass would not draw Arial Bold (no Arial or metric twin on
the machine): another face's ink is another size.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg

from creation.captions import Cue, build_ass, burn_ass, find_house_font
from creation.post.letterbox import TitleBlock, mark_and_title, pad_to_canvas

TOLERANCE = 4
#: The reference's ink, (first, last) row or column.
# The mark (the header-logo "C", 27 Oct 2026) sits inside the picture, top left, since fictora-drama
# #1133 (28 Oct 2026): its ink is this share of its drawn box (x 7-222 of 230, y 10-60 of 74, as measured
# on the 230x74 mark), so the expectation follows the mark's place and size, not a pinned number.
MARK_INK_X, MARK_INK_Y = (7 / 230, 222 / 230), (10 / 74, 60 / 74)
TITLE_1, TITLE_2 = (333, 384), (396, 435)
CAPTION_DESCENDER, CAPTION_PLAIN = (1417, 1468), (1417, 1457)
#: fictora-drama #614's measured renders of two captions' ink columns: one centred on the frame, one
#: shifted left so its layout ends on x 950.
CENTRED_COLS, SHIFTED_COLS = (259, 818), (103, 943)
#: A caption that clears x 950 centres on the frame (x 540), like Not Home.
CAPTION_CENTRE = 540

needs_arial = pytest.mark.skipif(
    find_house_font().path is None,
    reason="no Arial Bold (or metric twin) for libass: the ink would be another face's",
)


def _gray(video: Path, at: float) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080)


def _runs(mask: np.ndarray, offset: int) -> list[tuple[int, int]]:
    rows = np.flatnonzero(mask)
    if not rows.size:
        return []
    runs, start = [], rows[0]
    for a, b in zip(rows, rows[1:]):
        if b > a + 1:
            runs.append((offset + int(start), offset + int(a)))
            start = b
    runs.append((offset + int(start), offset + int(rows[-1])))
    return runs


def _near(got: tuple[int, int], want: tuple[int, int]) -> bool:
    return abs(got[0] - want[0]) <= TOLERANCE and abs(got[1] - want[1]) <= TOLERANCE


@needs_ffmpeg
@needs_arial
def test_the_mark_title_and_captions_land_on_not_homes_ink(tmp_path: Path) -> None:
    picture = tmp_path / "picture.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=256x192:d=4:r=24",
         "-pix_fmt", "yuv420p", str(picture)],
        check=True,
    )  # fmt: skip
    canvas = pad_to_canvas(picture, tmp_path / "canvas.mp4")
    ass = tmp_path / "captions.ass"
    cues = [
        Cue(0.0, 1.0, "Don't open it."), Cue(1.0, 2.0, "I've been in here"),
        Cue(2.0, 3.0, "Who are you texting?"), Cue(3.0, 4.0, "Previous owner was my mother!"),
    ]  # fmt: skip
    from creation.captions import LetterboxBand
    from creation.post.delivery_geometry import caption_colour_code, layout

    place = layout()
    band = LetterboxBand(
        place.caption.x, place.caption.right, place.caption.y, place.caption_highest_top,
        place.caption.bottom, place.caption_size, place.caption_min_size,
    )  # fmt: skip
    ass.write_text(
        build_ass(
            cues,
            width=1080,
            height=1920,
            band=band,
            colour=caption_colour_code("white"),
        ),
        encoding="utf-8",
    )
    captioned = tmp_path / "captioned.mp4"
    burn_ass("ffmpeg", canvas, ass, captioned)
    marked, fitted = mark_and_title(
        captioned, tmp_path / "marked.mp4",
        title=TitleBlock("POV: your roommate texted", '"Don\'t come home."'),
    )  # fmt: skip
    assert fitted is not None and len(fitted.setup) == 1 and len(fitted.hook) == 1

    first = _gray(marked, 0.5)
    from creation.post.watermark import mark_size

    mx, my = place.mark
    mw, mh = mark_size(1080)
    want_x = (mx + round(mw * MARK_INK_X[0]), mx + round(mw * MARK_INK_X[1]))
    want_y = (my + round(mh * MARK_INK_Y[0]), my + round(mh * MARK_INK_Y[1]))
    mark = first[my - 20 : my + mh + 20, 0 : mx + mw + 40] > 150
    xs, ys = np.flatnonzero(mark.any(0)), np.flatnonzero(mark.any(1))
    assert _near((int(xs[0]), int(xs[-1])), want_x), (xs[0], xs[-1])
    assert _near((my - 20 + int(ys[0]), my - 20 + int(ys[-1])), want_y), (ys[0], ys[-1])
    assert (first[150:260, 0:300] > 150).sum() == 0, (
        "nothing in the band where the mark was"
    )

    title = _runs((first[260:555] > 150).any(1), 260)
    assert len(title) == 2, title
    assert _near(title[0], TITLE_1), title
    assert _near(title[1], TITLE_2), title

    caption = first[1366:1600] > 150
    rows = _runs(caption.any(1), 1366)
    assert len(rows) == 1 and _near(rows[0], CAPTION_DESCENDER), rows
    cols = np.flatnonzero(caption.any(0))
    assert abs((cols[0] + cols[-1]) / 2 - CAPTION_CENTRE) <= TOLERANCE, (
        cols[0],
        cols[-1],
    )

    second = _gray(marked, 1.5)
    rows = _runs((second[1366:1600] > 150).any(1), 1366)
    assert len(rows) == 1 and _near(rows[0], CAPTION_PLAIN), rows

    for at, want in ((2.5, CENTRED_COLS), (3.5, SHIFTED_COLS)):
        cols = np.flatnonzero((_gray(marked, at)[1366:1600] > 150).any(0))
        assert _near((int(cols[0]), int(cols[-1])), want), (at, cols[0], cols[-1])
