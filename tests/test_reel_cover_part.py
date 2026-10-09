"""The cover's "PART N" is the series episode number, and a letterbox cover keeps clear of the bands.

NOCLIP (L-20261008-9, 8-9 Oct 2026): each episode lives on its own desk, so
every desk's episode is 1 and every auto cover said "PART 1"; and the cover's
big title sat on the letterbox title band. The operator made covers by hand.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg
from test_reel import reel_desk  # noqa: F401  (fixture)

from creation.ops.floor import init_series_desk
from creation.post.reel_cover import (
    cover_layout,
    remember_part,
    series_part,
)
from reel_fake_server import FakeReelServer

# --- which number --------------------------------------------------------------------------------


def test_a_one_episode_desk_named_for_its_episode_is_that_part(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Noclip Ep03", band="15s", episode_count=1)
    assert "ep03" in desk.name
    found = series_part(desk, 1)
    assert (found.number, found.source) == (3, "desk name")
    assert "PART 3" in found.note() and "--part" in found.note()


def test_a_desk_holding_the_whole_series_keeps_its_own_ordinal(tmp_path: Path) -> None:
    # Named like an episode, but it holds several: the ordinal is the series number, as before.
    desk = init_series_desk(tmp_path, "Show Ep03", band="15s", episode_count=2)
    assert series_part(desk, 1).number == 1
    assert series_part(desk, 2).number == 2
    plain = init_series_desk(tmp_path, "Sweet Racket", band="15s", episode_count=1)
    found = series_part(plain, 1)
    assert (found.number, found.from_desk_ordinal) == (1, True)


def test_the_saved_number_and_the_flag_win(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Noclip Ep03", band="15s", episode_count=2)
    remember_part(desk, 1, 7)
    saved = json.loads((desk / "production.config.json").read_text(encoding="utf-8"))
    assert saved["first_part"] == 7
    assert (series_part(desk, 1).number, series_part(desk, 2).number) == (7, 8)
    assert series_part(desk, 1).source == "desk setting"
    assert series_part(desk, 2, override=4).number == 4
    with pytest.raises(ValueError):
        remember_part(desk, 3, 2)  # episode 1 would be part 0
    with pytest.raises(ValueError):
        series_part(desk, 1, override=0)


def test_the_cli_part_flag_saves_it_on_the_desk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation.cli_produce import main

    desk = init_series_desk(tmp_path, "Noclip", band="15s", episode_count=1)
    monkeypatch.setattr("creation.post.reel.run_reel", lambda desk, **kwargs: None)
    assert main(["reel", "--desk", str(desk), "--episode", "1", "--part", "4"]) == 0
    assert series_part(desk, 1).number == 4
    assert main(["reel", "--desk", str(desk), "--episode", "1", "--part", "0"]) == 1


# --- what the server is sent, and the books -----------------------------------------------------


def test_operator_body_sends_part_only_when_given() -> None:
    from creation.post.reel_via_server import BedChoice, operator_body

    common = dict(
        takes=[], bed=BedChoice(None, -16.5, None, False, ""), bed_upload=None, still_upload=None,
        caption_style="house", pov=False, seconds=15.0, ending=None, hook_line=None, no_hook_line=False,
        hook_line_position=None, no_cover=False, cover_frame=None, plan=None,
    )  # fmt: skip
    assert "part" not in operator_body(**common)["operator"]
    assert operator_body(**common, part=3)["operator"]["part"] == 3


@needs_ffmpeg
def test_the_reel_asks_the_server_for_the_series_part_and_books_it(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
) -> None:
    from creation.post.reel import run_reel

    first = run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())
    assert first.video is not None
    assert "part" not in reel_server.requests[-1]["operator"]  # nothing said: as before

    remember_part(reel_desk, 1, 5)
    out = io.StringIO()
    made = run_reel(reel_desk, episode=1, seconds=6.0, stream=out)
    assert made.video is not None
    assert reel_server.requests[-1]["operator"]["part"] == 5
    assert "PART 5" in out.getvalue()
    rows = list(
        csv.DictReader((reel_desk / "reels" / "metrics.csv").open(encoding="utf-8"))
    )
    assert [r["part"] for r in rows] == ["1", "5"]


# --- where the text goes ----------------------------------------------------------------------------


def _shape(layout: object) -> tuple[object, ...]:
    return (
        layout.placement,
        layout.part_text,
        layout.part_size,
        layout.title_lines,  # type: ignore[attr-defined]
        layout.title_size,
        layout.centre_x,
        layout.top_px,
        layout.part_top_px,  # type: ignore[attr-defined]
        tuple(round(b, 4) for b in layout.box),  # type: ignore[attr-defined]
    )


def test_portrait_covers_are_laid_out_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Widths come from whatever font the machine has (Arial on a Mac, the fallback on CI),
    # so a hard-coded box differs between a laptop and CI. Measure with the fixed Arial
    # Bold advance table instead; the expected tuples are origin/main run under this same
    # table, so this compares the old and new code paths, not machines.
    import creation.captions as captions_module
    import creation.post.hook_overlay as hook_overlay_module
    import creation.post.reel_cover as reel_cover_module
    from creation.post.delivery_geometry import arial_bold_width

    for module in (captions_module, hook_overlay_module, reel_cover_module):
        monkeypatch.setattr(module, "text_width", arial_bold_width)
    assert _shape(cover_layout(series="Noclip", part=3, width=1080, height=1920)) == (
        "lower", "PART 3", 75, ("Noclip",), 150, 475, 1260, 1432, (0.2499, 0.6562, 0.6298, 0.7849),
    )  # fmt: skip
    assert _shape(
        cover_layout(
            series="The Very Long Title Of A Show", part=12, width=1080, height=1920,
            faces=[(0.3, 0.6, 0.3, 0.2)],
        )
    ) == (
        "upper", "PART 12", 62, ("The Very Long", "Title Of A Show"), 125, 475, 269, 538,
        (0.0571, 0.1401, 0.8225, 0.3125),
    )  # fmt: skip


@pytest.mark.parametrize("faces", [(), [(0.3, 0.55, 0.3, 0.15)]])
@pytest.mark.parametrize(
    "series", ["Noclip", "The Very Long Title Of A Backrooms Show Indeed", ""]
)
def test_a_letterbox_cover_never_touches_the_title_or_caption_band(
    series: str, faces: list[tuple[float, float, float, float]]
) -> None:
    from creation.post.delivery_geometry import layout as letterbox_layout

    place = letterbox_layout()
    rows = (place.picture.y, place.picture.bottom)
    laid = cover_layout(
        series=series, part=4, width=1080, height=1920, faces=faces, picture_rows=rows
    )
    top = laid.top_px
    bottom = laid.part_top_px + laid.part_size
    # The title band ends where the picture starts (y 555); the caption band starts under it (y 1365).
    assert place.title.bottom < rows[0] <= top
    assert bottom <= rows[1] < place.caption.y
    assert laid.part_text == "PART 4"


@needs_ffmpeg
def test_a_4_3_cover_picture_goes_on_the_letterbox_canvas_uncropped(
    tmp_path: Path,
) -> None:
    from creation.post.reel import _letterbox_cover_picture

    clip = tmp_path / "take.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=160x120:d=1:r=24",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    canvas, at, width, height, rows = _letterbox_cover_picture(
        clip, 0.5, 160, 120, tmp_path
    )
    assert (width, height, rows, at) == (1080, 1920, (555, 1365), None)
    from PIL import Image

    with Image.open(canvas) as image:
        assert image.size == (1080, 1920)
        grey = image.convert("L")
        assert grey.getpixel((540, 300)) < 20  # the title band stays black
        assert grey.getpixel((540, 960)) > 230  # the whole picture, not a crop of it
        assert grey.getpixel((5, 960)) > 230 and grey.getpixel((1074, 960)) > 230


def test_a_block_taller_than_the_picture_shrinks_to_fit_between_the_bands() -> None:
    rows = (555, 855)
    laid = cover_layout(
        series="The Very Long Title Of A Show",
        part=4,
        width=1080,
        height=1920,
        picture_rows=rows,
    )
    assert rows[0] <= laid.top_px and laid.part_top_px + laid.part_size <= rows[1]
