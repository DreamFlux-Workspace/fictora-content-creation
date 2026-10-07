"""Write a prop's real words on it in ``finish`` (fictora-drama prop_text), never the model's letters.

Lost and Found canary (7 Oct 2026, ``take-ep01-t1-raw-v1``, shot 3, 7-11 s): "a
note in her late mother's handwriting" was filmed with made-up English
("Thaen sl4t") over nonsense Chinese characters.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg
from PIL import Image, ImageChops
from test_story_sign_overlay import SHOTS, _finish

from creation.captions import FONT_NAME, FONTS_DIR, burn_ass
from creation.picture_checks import take_picture_check_lines
from creation.post.edit import BlurBox
from creation.post.prop_text import (
    HANGUL_HAND,
    JAPANESE_HAND,
    LATIN_HAND,
    PropOverlay,
    PropTextRequest,
    facts_props,
    fit_words,
    handwriting_face,
    parse_prop_text,
    plan_prop_overlays,
    prop_text_ass,
    suggestion_lines,
)

WORDS = "I kept it dry for you. — Mom"
NOTE = {
    "shot_index": 1,
    "prop": "a folded note in her late mother's handwriting",
    "text": WORDS,
}
PHONE = {"shot_index": 2, "prop": "Dev's phone with a text message", "text": ""}
BOX = BlurBox(40, 300, 220, 110)
SIZE = (384, 672)


def _facts(*props: dict) -> dict:
    body: dict = {"shots": SHOTS}
    if props:
        body["prop_text"] = list(props)
    return {"take_facts": body}


# --- parsing and planning ---------------------------------------------------------------------


def test_prop_text_reads_words_and_a_shot() -> None:
    assert parse_prop_text(f"{WORDS}@3") == PropTextRequest(WORDS, 3)
    assert parse_prop_text("엄마가 남긴 쪽지") == PropTextRequest(
        "엄마가 남긴 쪽지", None
    )
    assert parse_prop_text("@1") == PropTextRequest("", 1)
    with pytest.raises(ValueError, match="never makes words up"):
        parse_prop_text("  ")


def test_the_storys_words_come_from_the_take_facts_for_the_shot() -> None:
    [overlay], notes = plan_prop_overlays(
        _facts(NOTE, PHONE), [PropTextRequest("", 1)], [BOX]
    )

    assert notes == []
    assert (overlay.text, overlay.shot_index, overlay.start, overlay.end) == (
        WORDS,
        1,
        0.0,
        1.8,
    )
    assert overlay.box == (40, 300, 220, 110)
    assert overlay.family == LATIN_HAND[0]
    assert "a folded note" in overlay.describe()


def test_given_words_find_their_shot_and_win_over_the_facts() -> None:
    [overlay], _ = plan_prop_overlays(
        _facts(NOTE), [PropTextRequest("Back soon", None)], [BOX]
    )
    assert (overlay.text, overlay.shot_index) == ("Back soon", 1)
    [phone], _ = plan_prop_overlays(
        _facts(NOTE, PHONE), [PropTextRequest("곧 갈게", 2)], [BOX]
    )
    assert (phone.text, phone.family) == ("곧 갈게", HANGUL_HAND[0])


def test_no_words_means_nothing_is_written() -> None:
    overlays, notes = plan_prop_overlays(
        _facts(NOTE, PHONE), [PropTextRequest("", 2)], [BOX]
    )
    assert overlays == []
    assert (
        "the story gives no words for shot 2" in notes[0] and "blur --box" in notes[0]
    )
    # An older server (no prop_text) and no words given: nothing either.
    overlays, notes = plan_prop_overlays(_facts(), [PropTextRequest("", 1)], [BOX])
    assert overlays == [] and notes


def test_no_box_or_no_shot_means_nothing_is_written() -> None:
    overlays, notes = plan_prop_overlays(_facts(NOTE), [PropTextRequest("", 1)], [])
    assert overlays == [] and "--prop-box x,y,w,h" in notes[0]
    # The facts' own box, when a server sends one, is used.
    boxed = {**NOTE, "box": [10, 20, 100, 50]}
    [overlay], _ = plan_prop_overlays(_facts(boxed), [PropTextRequest("", 1)], [])
    assert overlay.box == (10, 20, 100, 50)
    overlays, notes = plan_prop_overlays(
        _facts(NOTE, {**NOTE, "shot_index": 2}), [PropTextRequest("Hi", None)], [BOX]
    )
    assert overlays == [] and "add @SHOT" in notes[0]


def test_facts_props_skips_broken_entries() -> None:
    raw = _facts(
        NOTE,
        {"shot_index": "1", "prop": "x"},
        {"shot_index": 1, "prop": ""},
        {"shot_index": True, "prop": "y"},
    )
    assert [p.prop for p in facts_props(raw)] == [NOTE["prop"]]
    assert facts_props(None) == [] and facts_props({"take_facts": {}}) == []


def test_the_face_follows_the_script() -> None:
    assert handwriting_face(WORDS) == (LATIN_HAND[0], "")
    assert handwriting_face("엄마가 남긴 쪽지") == (HANGUL_HAND[0], "")
    assert handwriting_face("母の手紙") == (JAPANESE_HAND[0], "")
    family, note = handwriting_face("妈妈的信")
    assert family == JAPANESE_HAND[0] and "system CJK face" in note
    for _family, filename in (LATIN_HAND, HANGUL_HAND, JAPANESE_HAND):
        assert (FONTS_DIR / filename).is_file(), filename
        assert (FONTS_DIR / f"OFL-{filename.split('-')[0]}.txt").is_file(), filename


def test_a_missing_face_falls_back_to_the_house_font_and_says_so(
    tmp_path: Path,
) -> None:
    family, note = handwriting_face(WORDS, fonts_dir=tmp_path)
    assert family == FONT_NAME and "house font" in note


def test_long_words_wrap_to_fill_the_box() -> None:
    lines, size = fit_words(WORDS, 220, 110)
    assert len(lines) >= 2 and " ".join(lines) == WORDS
    one_line, one_size = fit_words("Mom", 220, 110)
    assert one_line == ["Mom"] and one_size > size


def test_the_ass_draws_only_in_the_shot_window_on_the_box() -> None:
    [overlay], _ = plan_prop_overlays(_facts(NOTE), [PropTextRequest("", 1)], [BOX])
    ass = prop_text_ass([overlay], width=384, height=672)
    assert "\\pos(40,300)" in ass and "m 0 0 l 220 0 220 110 0 110" in ass
    assert f"\\fn{LATIN_HAND[0]}" in ass
    assert "0:00:00.00,0:00:01.80" in ass and ass.count("Dialogue:") == 2


def test_finish_prints_the_facts_props_as_a_suggestion() -> None:
    lines = suggestion_lines(_facts(NOTE, PHONE), desk="desk", episode=1, take_id="t1")
    assert f'shot 1: {NOTE["prop"]} (the story\'s words: "{WORDS}")' in lines[0]
    assert "the story gives no words" in lines[1]
    assert '--prop-text "@1" --prop-box x,y,w,h' in lines[-1]
    assert suggestion_lines(_facts(), desk="d", episode=1, take_id="t1") == []


# --- the picture-check printer ------------------------------------------------------------------


def test_lettering_findings_print_with_how_to_fix_them() -> None:
    facts = {
        "take_facts": {
            "picture_checks": [
                {
                    "kind": "garbled_text",
                    "severity": "warn",
                    "where": {"shot": 3},
                    "message": 'shot 3: made-up Latin letters on the folded note: "Thaen sl4t"; '
                    "write the real words on it or blur it",
                }
            ]
        }
    }
    lines = take_picture_check_lines(facts, take_id="t1")
    assert lines[0].startswith(
        '!! picture check: t1: shot 3: made-up Latin letters on the folded note: "Thaen sl4t"'
    )
    assert (
        "lettering on a prop or sign (shot 3)" in lines[1] and "--prop-text" in lines[1]
    )
    assert lines[-1].startswith("   look before approving")
    plain = {
        "take_facts": {
            "picture_checks": [
                {"kind": "head_count", "where": {"shot": 1}, "message": "m"}
            ]
        }
    }
    assert not any(
        "--prop-text" in line for line in take_picture_check_lines(plain, take_id="t1")
    )


# --- drawn on a tiny clip -------------------------------------------------------------------------


def _clip(path: Path, size: tuple[int, int] = SIZE) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x968c82:s={size[0]}x{size[1]}:d=3:r=12",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _frame(video: Path, at: float, out: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at}", "-i", str(video), "-frames:v", "1", str(out)],
                   check=True)  # fmt: skip
    return Image.open(out).convert("L")


def _drawn(
    tmp_path: Path, text: str, family: str, name: str, box: BlurBox = BOX
) -> Image.Image:
    """The box of a frame inside shot 1 with ``text`` written in ``family``."""

    overlay = PropOverlay(
        text, 1, "a note", (box.x, box.y, box.w, box.h), 0.0, 1.8, family
    )
    ass = tmp_path / f"{name}.ass"
    ass.write_text(
        prop_text_ass([overlay], width=SIZE[0], height=SIZE[1]), encoding="utf-8"
    )
    out = tmp_path / f"{name}.mp4"
    burn_ass("ffmpeg", _clip(tmp_path / f"{name}-base.mp4"), ass, out)
    frame = _frame(out, 0.9, tmp_path / f"{name}.png")
    return frame.crop((box.x, box.y, box.x + box.w, box.y + box.h))


def _ink(crop: Image.Image) -> int:
    """Ink pixels: clearly darker than the paper plate (about 236)."""

    return int((np.asarray(crop) < 170).sum())


def _differs(a: Image.Image, b: Image.Image) -> int:
    return int((np.asarray(ImageChops.difference(a, b)) > 60).sum())


@needs_ffmpeg
def test_the_words_are_written_in_the_box_only_for_the_shot(tmp_path: Path) -> None:
    [overlay], _ = plan_prop_overlays(_facts(NOTE), [PropTextRequest("", 1)], [BOX])
    ass = tmp_path / "note.ass"
    ass.write_text(
        prop_text_ass([overlay], width=SIZE[0], height=SIZE[1]), encoding="utf-8"
    )
    out = tmp_path / "note.mp4"
    burn_ass("ffmpeg", _clip(tmp_path / "base.mp4"), ass, out)
    inside = _frame(out, 0.9, tmp_path / "in.png")
    later = _frame(out, 2.5, tmp_path / "later.png")
    crop = inside.crop((BOX.x, BOX.y, BOX.x + BOX.w, BOX.y + BOX.h))
    assert _ink(crop) > 300, "the words are written on the prop"
    assert int((np.asarray(crop) > 200).sum()) > crop.width * crop.height // 3, (
        "on a paper plate"
    )
    # Outside the box and outside the shot the picture is as filmed (grey 0x968c82 is ~142).
    assert abs(inside.getpixel((BOX.x + BOX.w + 40, BOX.y + BOX.h // 2)) - 142) < 12
    assert abs(later.getpixel((BOX.x + BOX.w // 2, BOX.y + BOX.h // 2)) - 142) < 12


@needs_ffmpeg
@pytest.mark.parametrize(
    ("first", "second", "face"),
    [
        # Same length, so an empty box per character would lay out identically.
        ("엄마가", "보고파", HANGUL_HAND[0]),
        ("ありがとう", "さようなら", JAPANESE_HAND[0]),
        ("母の手紙", "雨の日に", JAPANESE_HAND[0]),
    ],
)
def test_cjk_words_render_as_real_glyphs_not_empty_boxes(
    tmp_path: Path, first: str, second: str, face: str
) -> None:
    assert handwriting_face(first)[0] == face
    one = _drawn(tmp_path, first, face, "one")
    two = _drawn(tmp_path, second, face, "two")
    # Glyphs are drawn (ink on the plate) ...
    assert _ink(one) > 150 and _ink(two) > 150
    # ... and they are the letters, not tofu: an empty box looks the same for every
    # character, so two different words would draw near-identical ink.
    assert _differs(one, two) > 300
    # ... in the handwriting face, not the system fallback.
    house = _drawn(tmp_path, first, FONT_NAME, "house")
    assert _differs(one, house) > 200


@needs_ffmpeg
def test_finish_writes_the_storys_words_on_the_prop(post_desk: Path) -> None:
    result, printed = _finish(
        post_desk,
        _facts(NOTE),
        prop_texts=(PropTextRequest("", 1),),
        prop_boxes=(BlurBox(20, 400, 200, 100),),
    )

    step = next(s for s in result.steps if s.step == "prop-text")
    assert step.status == "ran" and step.output is not None, printed
    assert (post_desk / "ep01" / "takes" / "take-ep01-t1-prop-v1.ass").is_file()
    frame = np.asarray(
        _frame(step.output, 0.5, post_desk.parent / "prop.png").crop(
            (20, 400, 220, 500)
        )
    )
    assert int((frame < 90).sum()) > 300, "dark ink written in shot 1"
    assert int((frame > 200).sum()) > 200 * 100 // 3, "on a paper plate"
    later = np.asarray(
        _frame(step.output, 2.5, post_desk.parent / "prop-later.png").crop(
            (20, 400, 220, 500)
        )
    )
    assert int((later > 200).sum()) < 20 and int((later < 90).sum()) < 20, (
        "shot 2 is left as filmed"
    )


@needs_ffmpeg
def test_finish_without_prop_text_only_suggests_and_never_invents(
    post_desk: Path,
) -> None:
    result, printed = _finish(post_desk, _facts(NOTE, PHONE))
    assert "prop-text" not in [s.step for s in result.steps]
    assert (
        "[prop-text] prop with writing: shot 1" in printed
        and '--prop-text "@1"' in printed
    )

    result, printed = _finish(
        post_desk,
        _facts(NOTE, PHONE),
        prop_texts=(PropTextRequest("", 2),),
        prop_boxes=(BOX,),
    )
    assert "prop-text" not in [s.step for s in result.steps]
    assert (
        "[prop-text] !! " in printed
        and "the story gives no words for shot 2" in printed
    )
