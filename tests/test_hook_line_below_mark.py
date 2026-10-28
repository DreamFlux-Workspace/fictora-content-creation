"""A portrait episode's top hook line starts under the Sokii mark, never on it, where the server says.

Founder screenshot, 28 Oct 2026: "One clean minute could save him" was drawn
right over the mark in the top-left. The server now owns both places
(fictora-drama #1133, ``POST /v1/mark/placement``): the mark's top-left corner,
its drawn size (``mark_width`` x ``mark_height``, 207x67 on 1080x1920) and
``hook_top``, where a top hook line starts (the mark's bottom plus 2.5%). The kit only draws what it is told; with an older server or
none it uses the same numbers itself.

Checked here: the kit draws exactly the server's mark (place and size) and
starts the line at the server's ``hook_top``; an answer without the new fields
falls back cleanly; and the line's box (outline included, two lines at full
size) never touches the mark's box on 1080x1920 and 768x1344, by the server's
numbers and by the kit's, with a rendered libass check where ffmpeg has it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from creation.caption_server import server_override
from creation.captions import CAPTION_BAND
from creation.post import hook_overlay as hk
from creation.post import watermark as wm
from creation.post.hook_overlay import HookOverlay, overlay_ass
from creation.post.watermark import MarkPlace, fallback_mark_place, mark_place

SIZES = [(1080, 1920), (768, 1344)]


def _served(width: int, height: int) -> dict[str, int]:
    """What fictora-drama #1133 answers for a portrait frame. Derived from the kit's fallback
    constants, which mirror the server's (so a later move of the mark, e.g. further in for
    Instagram's side crop, changes one constant, not these tests)."""

    place = fallback_mark_place(width, height)
    return {
        "x": place.x, "y": place.y, "mark_width": place.width,
        "mark_height": place.height, "hook_top": place.hook_top,
    }  # fmt: skip


SERVER = {size: _served(*size) for size in SIZES}
#: The founder's line (two lines at 88 px on 1080), and a longer one: two lines is the tallest a top line gets.
LONG = "One clean minute could save him from the whole family"
SHORT = "One clean minute could save him"


def _answer(extra: dict[str, Any] | None = None, *, drop: tuple[str, ...] = ()):
    def poster(_path: str, body: dict[str, Any]) -> tuple[int, Any]:
        answer = {
            **SERVER[(body["width"], body["height"])],
            "opacity": wm.MARK_ALPHA,
            **(extra or {}),
        }
        return 200, {k: v for k, v in answer.items() if k not in drop}

    return poster


def _anchor(ass: str) -> tuple[str, int, int]:
    match = re.search(r"\\an(\d)\\pos\((\d+),(\d+)\)", ass)
    assert match, ass
    return match.group(1), int(match.group(2)), int(match.group(3))


def _lines(ass: str) -> int:
    return ass.rstrip().rsplit("}", 1)[1].count("\\N") + 1


def _hook(text: str, placement: str = "top") -> HookOverlay:
    return HookOverlay("hook", text, placement, 0.0, 3.0)  # type: ignore[arg-type]


# --- the kit draws what the server says ------------------------------------------------------------


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_kit_takes_the_servers_mark_place_size_and_hook_top(
    width: int, height: int
) -> None:
    served = SERVER[(width, height)]
    with server_override(_answer()):
        place = mark_place(width, height, desk=Path("/desk"))

    assert place == MarkPlace(
        served["x"], served["y"], served["mark_width"], served["mark_height"],
        served["hook_top"],
    )  # fmt: skip


def test_the_server_alone_decides_even_numbers_the_kit_would_never_pick() -> None:
    odd = {
        "x": 90,
        "y": 400,
        "mark_width": 300,
        "mark_height": 97,
        "hook_top": 555,
        "opacity": 0.4,
    }
    with server_override(_answer(odd)):
        place = mark_place(1080, 1920, desk=Path("/desk"))
    assert place == MarkPlace(90, 400, 300, 97, 555, 0.4)
    assert wm.mark_scale_filter((place.width, place.height)) == (
        "scale=300:97:flags=lanczos,"
    )


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_hook_line_is_burned_at_the_servers_hook_top(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, width: int, height: int
) -> None:
    from types import SimpleNamespace

    import creation.captions as captions
    import creation.post.media as media

    monkeypatch.setattr(
        media,
        "probe_video",
        lambda _v: SimpleNamespace(width=width, height=height, duration_seconds=4.0),
    )
    monkeypatch.setattr(captions, "find_ffmpeg", lambda: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(captions, "burn_ass", lambda *a, **k: None)
    ass = tmp_path / "hook.ass"
    # Not the kit's own number, so only the server's answer can put it there.
    served_top = SERVER[(width, height)]["hook_top"] + 37
    with server_override(_answer({"hook_top": served_top})):
        hk.burn(_hook(SHORT), tmp_path / "in.mp4", ass, tmp_path / "out.mp4", desk=Path("/desk"))  # fmt: skip

    assert _anchor(ass.read_text(encoding="utf-8"))[2] == served_top


def test_the_watermark_step_draws_the_servers_size_at_the_servers_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    ran: list[list[str]] = []
    monkeypatch.setattr(
        wm,
        "probe_video",
        lambda _v: SimpleNamespace(width=1080, height=1920, has_audio=False),
    )
    monkeypatch.setattr(wm, "run_ffmpeg", lambda args: ran.append(args))
    odd = {"x": 90, "y": 400, "mark_width": 300, "mark_height": 97, "opacity": 0.4}
    with server_override(_answer(odd)):
        wm.watermark(tmp_path / "in.mp4", tmp_path / "out.mp4", desk=Path("/desk"))

    graph = ran[0][ran[0].index("-filter_complex") + 1]
    assert "scale=300:97:flags=lanczos," in graph and "overlay=90:400" in graph
    assert "colorchannelmixer=aa=0.4[m]" in graph, "the server's opacity"
    # No opacity in the answer (an older server), or no server: the kit's own.
    with server_override(_answer(drop=("opacity",))):
        wm.watermark(tmp_path / "in.mp4", tmp_path / "out2.mp4", desk=Path("/desk"))
    wm.watermark(tmp_path / "in.mp4", tmp_path / "out3.mp4")
    for args in ran[1:]:
        assert (
            f"colorchannelmixer=aa={wm.MARK_ALPHA}[m]"
            in args[args.index("-filter_complex") + 1]
        )


# --- an older server, or none ----------------------------------------------------------------------


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_an_answer_without_the_new_fields_falls_back_cleanly(
    width: int, height: int
) -> None:
    with server_override(_answer(drop=("mark_width", "mark_height", "hook_top"))):
        place = mark_place(width, height, desk=Path("/desk"))
    kit = fallback_mark_place(width, height)

    assert (place.x, place.y) == (
        SERVER[(width, height)]["x"],
        SERVER[(width, height)]["y"],
    )
    assert (place.width, place.height) == (kit.width, kit.height)
    assert place.hook_top == place.y + kit.height + round(height * wm.HOOK_GAP_FRACTION)


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_no_server_uses_the_kits_copy_of_the_same_numbers(
    width: int, height: int
) -> None:
    with server_override(lambda _p, _b: (404, {"detail": "Not Found"})):
        place = mark_place(width, height, desk=Path("/desk"))
    served = SERVER[(width, height)]

    assert place == fallback_mark_place(width, height)
    assert (
        place.hook_top
        == served["hook_top"]
        == (place.y + place.height + round(height * wm.HOOK_GAP_FRACTION))
    )


# --- never on the mark -----------------------------------------------------------------------------


def _places(width: int, height: int) -> list[MarkPlace]:
    served = SERVER[(width, height)]
    return [
        MarkPlace(served["x"], served["y"], served["mark_width"], served["mark_height"], served["hook_top"]),
        fallback_mark_place(width, height),
    ]  # fmt: skip


@pytest.mark.parametrize(("width", "height"), SIZES)
@pytest.mark.parametrize("text", [SHORT, LONG])
def test_the_top_hook_lines_box_never_touches_the_mark(
    width: int, height: int, text: str
) -> None:
    for place in _places(width, height):
        ass = overlay_ass(
            _hook(text), width=width, height=height, hook_top=place.hook_top
        )
        anchor, _, y = _anchor(ass)
        outline = hk._scaled(hk.HOOK_OUTLINE, height)
        shadow = hk._scaled(hk.HOOK_SHADOW, height)
        size = hk._scaled(hk.HOOK_FONT_SIZE, height)
        mark_bottom = place.y + place.height

        assert anchor == "8" and y == place.hook_top
        # The line spans the frame between the margins, so it shares the mark's columns; only rows
        # keep them apart: its top edge, outline included, is under the mark.
        assert y - outline > mark_bottom, (
            f"hook box top {y - outline} on the mark (y {place.y}-{mark_bottom}) at {width}x{height}"
        )
        lines = _lines(ass)
        assert lines <= 2
        # Two full-size lines (libass line height is under 1.25x the size) end well above the captions.
        assert y + lines * size * 1.25 + outline + shadow < CAPTION_BAND[0] * height


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_letterbox_title_bar_on_a_portrait_take_starts_under_the_mark(
    width: int, height: int
) -> None:
    """A letterbox show's portrait take gets the title bar and the portrait mark (9% down); the mark
    sat on the bar's corner and ran into a long series title (28 Oct 2026). The bar starts at the
    line's place under the mark."""

    bar = HookOverlay(
        "title_bar", SHORT, "bar", 0.0, None, title="The Midnight Ramen Debt Collectors"
    )
    for place in _places(width, height):
        ass = overlay_ass(
            bar, width=width, height=height, duration=2.0, hook_top=place.hook_top
        )
        band_top = int(re.search(r"\\an7\\pos\(0,(\d+)\)", ass).group(1))

        assert band_top > place.y + place.height
        assert band_top + round(hk.BAR_HEIGHT * height) < CAPTION_BAND[0] * height


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_lower_place_does_not_move(width: int, height: int) -> None:
    anchor, _, y = _anchor(
        overlay_ass(_hook(LONG, "lower"), width=width, height=height, hook_top=999)
    )

    assert anchor == "2"
    assert y == round((CAPTION_BAND[0] - hk.BAND_GAP) * height)


def test_the_face_check_band_starts_where_the_line_does() -> None:
    place = fallback_mark_place(1080, 1920)
    assert hk.HOOK_TOP * 1920 == place.hook_top
    assert hk.HOOK_TOP * 1920 > place.y + place.height


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
    served = SERVER[(width, height)]
    grey = tmp_path / "grey.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}:d=2:r=24",
         "-pix_fmt", "yuv420p", str(grey)],
        check=True,
    )  # fmt: skip
    ass = tmp_path / "hook.ass"
    ass.write_text(
        overlay_ass(_hook(LONG), width=width, height=height, hook_top=served["hook_top"]),
        encoding="utf-8",
    )  # fmt: skip
    out = tmp_path / "hook.mp4"
    burn_ass(ffmpeg, grey, ass, out)
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    frame = np.frombuffer(raw, np.uint8).reshape(height, width).astype(int)
    ink_rows = np.flatnonzero((np.abs(frame - 128) > 40).any(axis=1))
    mark_bottom = served["y"] + served["mark_height"]

    assert ink_rows.size, "the hook line was drawn"
    assert ink_rows[0] > mark_bottom, (
        f"ink from row {ink_rows[0]}, mark down to {mark_bottom}"
    )
    assert ink_rows[-1] < CAPTION_BAND[0] * height
