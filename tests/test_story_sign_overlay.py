"""A story sign the video garbled is overlaid with its exact words (follow-up to fictora-drama #583).

Take facts may carry ``story_signs: [{shot_index, text, where}]``. When the text check's
possible-lettering finding sits in a shot with one story sign, ``finish`` prints the overlay
it would draw instead of a blur, and ``finish --sign-overlay`` draws it. Facts without
``story_signs`` change nothing.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from conftest import needs_ffmpeg
from PIL import Image
from test_post_finish import fake_bed, fake_sfx
from test_take_text_sign_lettering import _fake_ocr, _findings, _set_video, _sign

from creation.post.finish import run_finish
from creation.post.story_signs import overlay_ass, plan_overlays, story_signs

SIZE = (384, 672)
SHOTS = [
    {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 1.8},
    {"shot_index": 2, "start_seconds": 1.8, "end_seconds": 3.0},
]
SIGN = {"shot_index": 1, "text": "OPEN", "where": "the shop's fascia"}


def _facts(*signs: dict) -> dict:
    body: dict = {"shots": SHOTS}
    if signs:
        body["story_signs"] = list(signs)
    return {"take_facts": body}


def test_lettering_in_a_shot_with_one_story_sign_is_planned_over_the_shot() -> None:
    [finding] = _findings(*[w for page in (1, 2, 3) for w in _sign(page)])

    [overlay], notes = plan_overlays(_facts(SIGN), [finding], SIZE)

    assert notes == []
    assert (overlay.sign.text, overlay.start, overlay.end) == ("OPEN", 0.0, 1.8)
    assert overlay.box == finding.blur_box(SIZE)
    ass = overlay_ass([overlay], width=384, height=672)
    x, y, w, h = overlay.box
    assert f"\\pos({x},{y})" in ass and f"m 0 0 l {w} 0 {w} {h} 0 {h}" in ass
    assert ass.rstrip().endswith("OPEN") and "0:00:01.80" in ass


def test_no_story_signs_or_two_in_one_shot_overlay_nothing() -> None:
    [finding] = _findings(*[w for page in (1, 2, 3) for w in _sign(page)])

    assert plan_overlays(_facts(), [finding], SIZE) == ([], [])
    assert story_signs({"take_facts": {"story_signs": [{"shot_index": 1}]}}) == []
    overlays, notes = plan_overlays(
        _facts(SIGN, {**SIGN, "text": "CLOSED"}), [finding], SIZE
    )
    assert overlays == [] and "2 story signs ('OPEN', 'CLOSED')" in notes[0]


def _finish(post_desk: Path, facts: dict, **kwargs):
    raw = post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(_set_video(post_desk.parent / "painted.mp4").read_bytes())
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    out = io.StringIO()
    result = run_finish(post_desk, facts_fetcher=lambda *a: None, text_ocr=_fake_ocr, sfx_render=fake_sfx([]),
                        bed_maker=fake_bed, thumbnail=False, colour=False, stream=out, **kwargs)  # fmt: skip
    return result, out.getvalue()


@needs_ffmpeg
def test_finish_suggests_the_overlay_without_the_flag(post_desk: Path) -> None:
    result, printed = _finish(post_desk, _facts(SIGN))

    summary = "\n".join(result.summary_lines())
    assert '!! possible garbled lettering on a story sign: shot 1 "OPEN"' in summary
    assert "--sign-overlay" in summary and "--sign-overlay" in printed
    assert "sign-overlay" not in [s.step for s in result.steps]


@needs_ffmpeg
def test_finish_without_story_signs_says_nothing_about_them(post_desk: Path) -> None:
    result, printed = _finish(post_desk, _facts())

    assert "story sign" not in printed and "--sign-overlay" not in "\n".join(
        result.summary_lines()
    )


@needs_ffmpeg
def test_finish_sign_overlay_draws_the_exact_words_on_a_plate_over_the_box(
    post_desk: Path,
) -> None:
    result, printed = _finish(post_desk, _facts(SIGN), sign_overlay=True)

    step = next(s for s in result.steps if s.step == "sign-overlay")
    assert step.status == "ran" and step.output is not None, printed
    assert (post_desk / "ep01" / "takes" / "take-ep01-t1-sign-v1.ass").is_file()
    frame = post_desk.parent / "frame.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(step.output), "-frames:v", "1",
                    str(frame)], check=True)  # fmt: skip
    before = post_desk.parent / "before.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "2.5", "-i", str(step.output), "-frames:v", "1",
                    str(before)], check=True)  # fmt: skip
    corner = (300 - 10, 137 - 8)  # inside the flagged box, on the light fascia before
    drawn, after_shot = (
        Image.open(frame).convert("RGB"),
        Image.open(before).convert("RGB"),
    )
    assert max(drawn.getpixel(corner)) < 70, (
        "the plate covers the garbled sign in its shot"
    )
    assert min(after_shot.getpixel(corner)) > 120, "shot 2 is left as filmed"
