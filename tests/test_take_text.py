"""The text check: subtitles the video model drew into a take are found; signs and noise are not; no OCR is said."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from conftest import make_take, needs_ffmpeg
from PIL import Image, ImageDraw, ImageFont
from test_post_finish import fake_bed, fake_sfx

from creation.post import take_text
from creation.post.finish import run_finish
from creation.post.review import review_take
from creation.post.take_text import (
    SKIPPED_NO_OCR,
    check_take_text,
    classify,
    parse_tsv,
    rows_of,
    sample_times,
    tesseract_runner,
)

SIZE = (384, 672)
LINES = [
    {
        "line_id": "line_01",
        "text": "Not for me. Keep the ring.",
        "subtitle": "Not for me. Keep the ring.",
    }
]
HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tsv(*words: tuple[int, int, int, int, int, float, str]) -> str:
    """TSV rows ``(page, left, top, width, height, conf, text)`` as tesseract writes them."""

    rows = [
        f"5\t{p}\t1\t1\t1\t1\t{x}\t{y}\t{w}\t{h}\t{c}\t{t}"
        for p, x, y, w, h, c, t in words
    ]
    return "\n".join([HEADER, *rows]) + "\n"


def _subtitle(
    page: int, top: int = 560
) -> list[tuple[int, int, int, int, int, float, str]]:
    return [(page, 90, top, 40, 22, 95.0, "Not"), (page, 136, top, 30, 22, 95.0, "for"),
            (page, 172, top, 36, 22, 94.0, "me."), (page, 214, top, 44, 22, 93.0, "Keep")]  # fmt: skip


# --- Reading rows (no OCR needed) ----------------------------------------------------------------------


def test_a_centred_row_in_the_caption_band_on_two_samples_is_a_drawn_subtitle() -> None:
    words = parse_tsv(_tsv(*_subtitle(2), *_subtitle(3)))
    times = [0.0, 0.5, 1.0, 1.5, 2.0]

    [finding] = classify(rows_of(words, SIZE), times, SIZE)

    assert finding.kind == "subtitle" and finding.times == [0.5, 1.0]
    assert finding.text == "Not for me. Keep"


def test_a_sign_in_every_frame_tiny_marks_and_low_confidence_noise_are_not_subtitles() -> (
    None
):
    sign = [
        w
        for p in range(1, 6)
        for w in (
            (p, 60, 80, 120, 30, 92.0, "HANAKAZE"),
            (p, 190, 80, 110, 30, 91.0, "SWEETS"),
        )
    ]
    band_sign = [
        w
        for p in range(1, 6)
        for w in (
            (p, 120, 600, 60, 20, 90.0, "OPEN"),
            (p, 186, 600, 60, 20, 90.0, "DAILY"),
        )
    ]
    tiny = [
        (2, 150, 560, 20, 5, 96.0, "badge"),
        (2, 175, 560, 20, 5, 96.0, "logo"),
    ]  # 5 px: a mark, not text
    unsure = [
        (3, 90, 560, 40, 22, 55.0, "wy"),
        (3, 136, 560, 30, 22, 61.0, "Ad"),
    ]  # hair strands read as letters
    words = parse_tsv(_tsv(*sign, *band_sign, *tiny, *unsure))

    findings = classify(rows_of(words, SIZE), [0.0, 0.5, 1.0, 1.5, 2.0], SIZE)

    assert [f.kind for f in findings] == ["text", "text"], (
        "signs are notes; the marks are dropped"
    )


def test_a_spoken_line_drawn_once_anywhere_is_a_subtitle() -> None:
    words = parse_tsv(_tsv(*_subtitle(1, top=100)))

    [finding] = classify(rows_of(words, SIZE), [0.0, 0.5], SIZE, lines=LINES)

    assert finding.kind == "subtitle" and finding.line_id == "line_01"


def test_spoken_line_windows_are_sampled_twice_as_often() -> None:
    assert sample_times(3.0, [(1.0, 2.0)]) == [0.0, 0.5, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]


# --- No OCR on the machine --------------------------------------------------------------------------------


@needs_ffmpeg
def test_without_ocr_the_check_says_it_was_skipped_never_a_silent_pass(
    tmp_path: Path,
) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=1.0)

    check = check_take_text(take)

    assert check.status == "skipped" and check.note == SKIPPED_NO_OCR
    assert check.warning_lines() == [f"!! {SKIPPED_NO_OCR}"]
    assert "text check skipped (no OCR installed)" in check.summary()


# --- Real frames, real tesseract --------------------------------------------------------------------------


def _video(path: Path, *, subtitle: str | None, seconds: float = 4.0) -> Path:
    """A 4 fps take: a dim set with a sign up top, and (1.0-3.0 s) a burned white subtitle in the band."""

    frames = path.parent / f"{path.stem}-frames"
    frames.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=26)
    small = ImageFont.load_default(size=6)
    for index in range(int(seconds * 4)):
        image = Image.new("RGB", SIZE, (70, 60, 55))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 200, 340, 520), fill=(110, 90, 80))
        for strand in range(8):  # hair strands
            draw.line(
                (150 + strand * 6, 220, 140 + strand * 9, 420),
                fill=(30, 25, 20),
                width=2,
            )
        draw.text(
            (60, 60), "LANTERN BAKERY", font=font, fill=(230, 210, 160)
        )  # the shop sign, every frame
        draw.text(
            (200, 300), "ID 07", font=small, fill=(255, 255, 255)
        )  # a badge: too small to be a line
        if subtitle and 1.0 <= index / 4 < 3.0:
            width = draw.textlength(subtitle, font=font)
            draw.text(((SIZE[0] - width) / 2, 560), subtitle, font=font, fill="white",
                      stroke_width=2, stroke_fill="black")  # fmt: skip
        image.save(frames / f"f{index:03d}.png")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-framerate", "4", "-i", str(frames / "f%03d.png"), "-r", "24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_burned_subtitles_are_found_with_times_and_a_crop_and_a_clean_take_passes(
    tmp_path: Path, real_ocr: str
) -> None:
    ocr = tesseract_runner(real_ocr)
    burned = _video(tmp_path / "burned.mp4", subtitle="Not for me. Keep the ring.")
    clean = _video(tmp_path / "clean.mp4", subtitle=None)

    found = check_take_text(burned, lines=LINES, sheet_dir=tmp_path / "takes", ocr=ocr)
    passed = check_take_text(clean, lines=LINES, sheet_dir=tmp_path / "takes", ocr=ocr)

    [subtitle] = found.subtitles
    assert subtitle.line_id == "line_01"
    assert subtitle.times and all(1.0 <= t < 3.0 for t in subtitle.times)
    assert found.sheet is not None and found.sheet.is_file()
    text = "\n".join(
        found.warning_lines(desk="D", episode=1, take_id="t2", final=Path("F.mp4"))
    )
    assert (
        text.startswith("!! DRAWN TEXT")
        and "1.00s" in text
        and str(found.sheet) in text
    )
    assert "fictora-produce film --desk D --episode 1 --take t2" in text
    assert "fictora-produce blur --desk D --take-file F.mp4 --box " in text
    assert passed.status == "clean" and not passed.subtitles, [
        f.text for f in passed.findings
    ]
    assert passed.sheet is None


# --- Where it shows: review and finish --------------------------------------------------------------------


def _fake_ocr(frames: list[Path], languages: str) -> str:
    """Subtitle words on the third and fourth samples of a 192x336 take."""

    words = [(p, 40 + i * 30, 270, 26, 14, 95.0, w) for p in (3, 4)
             for i, w in enumerate(("Not", "for", "me.", "Keep"))]  # fmt: skip
    return _tsv(*words)


@needs_ffmpeg
def test_review_reads_the_raw_take_and_warns_in_a_text_section(post_desk: Path) -> None:
    raw = make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")

    review = review_take(post_desk, take_file=raw, text_ocr=_fake_ocr)

    section = next(s for s in review.sections if s.name == "Text")
    assert section.status == "⚠" and "1 drawn subtitle group(s)" in section.summary
    assert any(
        d.startswith("'Not for me. Keep'") and "at 1.00s, 1.50s" in d
        for d in section.details
    ), section.details
    assert (post_desk / "ep01" / "takes" / "text-check-ep01-t1-v1.png").is_file()


@needs_ffmpeg
def test_review_without_ocr_says_the_text_check_was_skipped(post_desk: Path) -> None:
    raw = make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")

    section = next(
        s for s in review_take(post_desk, take_file=raw).sections if s.name == "Text"
    )

    assert section.status == "–" and section.summary == SKIPPED_NO_OCR


@needs_ffmpeg
def test_finish_summary_carries_the_drawn_text_warning(post_desk: Path) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps({"take_facts": {}})
    )
    out = io.StringIO()

    result = run_finish(post_desk, facts_fetcher=lambda *a: None, text_ocr=_fake_ocr, sfx_render=fake_sfx([]),
                        bed_maker=fake_bed, thumbnail=False, stream=out)  # fmt: skip

    summary = "\n".join(result.summary_lines())
    assert "!! DRAWN TEXT in the raw take `take-ep01-t1-raw-v1.mp4`" in summary
    assert f"--take-file {result.final}" in summary
    assert "!! DRAWN TEXT" in (post_desk / "ep01" / "run-notes.md").read_text()
    assert result.as_json()["text_warnings"]


def test_the_no_ocr_default_is_what_tests_see() -> None:
    assert take_text.tesseract_bin() is None
