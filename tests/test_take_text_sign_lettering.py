"""Possible lettering on a sign: unreadable glyphs held in one spot are a note, never a fault.

Hanakaze ep 7 (2026-10-01) drew garbled pseudo-kanji on two shop signs for 2.5 s.
The text check (#69) only looks for words: it keeps confident, mostly-letter
OCR in the caption band, so it said nothing. The same OCR pass also returns the
glyph boxes it was unsure of; a cluster of them, of letter height, high in the
frame, held in one spot for a second or more, is reported as "possible
lettering on a sign at t1-t2s" with the crop sheet. The fixture numbers are
tesseract's own boxes on the series' ep 6 take (a pseudo-lettered sign),
halved to the test frame. The board's blank hanging boards must stay quiet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import needs_ffmpeg
from PIL import Image, ImageDraw

from creation.post.review import review_take
from creation.post.take_text import (
    check_take_text,
    classify,
    parse_tsv,
    rows_of,
    sign_lettering,
)

SIZE = (384, 672)
TIMES = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
Box = tuple[int, int, int, int, int, float, str]


def _tsv(*words: Box) -> str:
    rows = [
        f"5\t{p}\t1\t1\t1\t1\t{x}\t{y}\t{w}\t{h}\t{c}\t{t}"
        for p, x, y, w, h, c, t in words
    ]
    return "\n".join([HEADER, *rows]) + "\n"


def _sign(page: int, *, dx: int = 0) -> list[Box]:
    """Two unsure glyph boxes on a fascia sign high in the frame (ep 6's, halved)."""

    texts = {
        1: ("2", "“bh"),
        2: ("Wy)", "cb"),
        3: ("vp", "cb"),
        4: ("2", "ob"),
        5: ("Wy", "cb"),
    }[page]
    return [
        (page, 300 + dx, 163, 32, 33, 18.0 + page, texts[0]),
        (page, 320 + dx, 137, 24, 28, 56.0, texts[1]),
    ]


def _blank_boards(page: int) -> list[Box]:
    """What OCR reads on a blank hanging board: its edges and one unsure mark."""

    return [(page, 40, 70, 3, 50, 40.0, "|"), (page, 128, 70, 3, 50, 41.0, "|"), (page, 40, 118, 90, 4, 10.0, "——"),
            (page, 80, 88, 14, 18, 30.0, "a")]  # fmt: skip


def _findings(*words: Box) -> list:
    parsed = parse_tsv(_tsv(*words))
    return sign_lettering(parsed, TIMES, SIZE, readable=rows_of(parsed, SIZE))


def test_unreadable_glyphs_held_on_a_sign_are_possible_lettering() -> None:
    words = [w for page in (1, 2, 3, 4) for w in _sign(page)]

    [finding] = _findings(*words)

    assert finding.kind == "sign_lettering"
    assert finding.times == [0.0, 0.5, 1.0, 1.5]
    # Not a subtitle: the word rules never see these boxes.
    assert classify(rows_of(parse_tsv(_tsv(*words)), SIZE), TIMES, SIZE) == []


def test_a_slow_camera_move_keeps_the_same_sign() -> None:
    words = [w for page in (1, 2, 3, 4) for w in _sign(page, dx=6 * page)]

    assert [f.times for f in _findings(*words)] == [[0.0, 0.5, 1.0, 1.5]]


def test_blank_hanging_boards_do_not_count() -> None:
    assert _findings(*[w for page in range(1, 6) for w in _blank_boards(page)]) == []


def test_a_flicker_a_sure_symbol_the_counter_and_mixed_sizes_do_not_count() -> None:
    flicker = [w for page in (1, 2) for w in _sign(page)]  # 0.5 s, two samples
    sure = [(p, 200, 170, 25, 31, 76.0, "xX") for p in (1, 2, 3, 4)] + [
        (p, 228, 172, 25, 30, 80.0, "Ae") for p in (1, 2, 3, 4)
    ]  # an anime anger mark OCR is sure of
    counter = [
        (p, x, 520, 30, 30, 20.0, t)
        for p in (1, 2, 3, 4)
        for x, t in ((40, "ee"), (72, "oe"))
    ]  # sweets
    mixed = [
        (p, 20, 20, 20, 20 + 30 * (p % 2), 25.0, t)
        for p in (1, 2, 3, 4)
        for t in ("wy",)
    ] + [
        (p, 42, 20, 20, 20 + 30 * (p % 2), 25.0, "cb") for p in (1, 2, 3, 4)
    ]  # a shape that grows and shrinks is not one lettering size
    lone = [
        (p, 300, 160, 30, 30, 20.0, "a") for p in (1, 2, 3, 4)
    ]  # one unsure letter alone

    brackets = [
        (p, x, 100, 14, 30, 30.0, t)
        for p in (1, 2, 3, 4)
        for x, t in ((120, "("), (136, ")"))
    ]  # hair edges read as brackets

    assert _findings(*flicker, *sure, *counter, *mixed, *lone, *brackets) == []


# --- Through the check, on real frames ----------------------------------------------------------------


def _set_video(path: Path) -> Path:
    """A 3 s, 4 fps take: two blank hanging boards and a fascia painted with pseudo-glyphs."""

    frames = path.parent / f"{path.stem}-frames"
    frames.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        image = Image.new("RGB", SIZE, (150, 140, 130))
        draw = ImageDraw.Draw(image)
        for left in (40, 250):  # blank hanging boards, as the board draws them
            draw.rectangle((left, 70, left + 90, 120), fill=(235, 230, 215))
        draw.rectangle((280, 120, 370, 210), fill=(225, 215, 190))  # the fascia
        for x in (298, 322):  # pseudo-glyph strokes
            for k in range(4):
                draw.line(
                    (x, 140 + 8 * k, x + 18, 136 + 9 * k), fill=(30, 25, 20), width=3
                )
                draw.line(
                    (x + 4 + 4 * k, 135, x + 2 + 4 * k, 192), fill=(30, 25, 20), width=2
                )
        image.save(frames / f"f{index:03d}.png")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-framerate", "4", "-i", str(frames / "f%03d.png"), "-r", "24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _fake_ocr(frames: list[Path], languages: str) -> str:
    """Unsure glyphs on the fascia and, as noise, on a blank board, on every sample."""

    board = [(p, 50, 80, 30, 30, 22.0, "wy") for p in range(1, len(frames) + 1)] + [
        (p, 84, 82, 28, 28, 25.0, "cb") for p in range(1, len(frames) + 1)
    ]
    sign = [
        (p, *box[1:]) for p in range(1, len(frames) + 1) for box in _sign(min(p, 5))
    ]
    return _tsv(*board, *sign)


@needs_ffmpeg
def test_the_check_notes_the_sign_with_a_crop_and_stays_clean(tmp_path: Path) -> None:
    take = _set_video(tmp_path / "set.mp4")

    check = check_take_text(take, sheet_dir=tmp_path / "takes", ocr=_fake_ocr)

    # A note, never a fault: the take is still clean of drawn subtitles.
    assert check.status == "clean" and not check.subtitles
    [finding] = (
        check.lettering
    )  # the blank board is flat: its "glyphs" are not painted on anything
    assert finding.box[0] >= 280
    assert "possible lettering on a sign at 0.00–2.50s" in check.summary()
    lines = check.note_lines()
    assert lines[0].startswith("possible lettering on a sign at 0.00–2.50s")
    assert (
        check.sheet is not None
        and check.sheet.is_file()
        and lines[-1] == f"crop: {check.sheet}"
    )
    assert check.warning_lines() == []
    assert check.as_json()["findings"][0]["kind"] == "sign_lettering"


@needs_ffmpeg
def test_review_lists_possible_lettering_without_a_warning(post_desk: Path) -> None:
    raw = post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(_set_video(post_desk.parent / "painted.mp4").read_bytes())

    review = review_take(post_desk, take_file=raw, text_ocr=_fake_ocr)

    section = next(s for s in review.sections if s.name == "Text")
    assert section.status == "✓", section.status
    assert any(
        d.startswith("possible lettering on a sign at") for d in section.details
    ), section.details
