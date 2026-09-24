"""Local house captions: line timing, flicker cues, ASS style."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from creation.captions import (
    Cue,
    Span,
    anchor_lines,
    build_ass,
    build_cues,
    caption_take,
    episode_lines,
    flicker_cues,
    parse_silencedetect,
    speech_spans,
    time_words,
)

# silencedetect output from a real H3 take: one line at 1.07–2.09 s, then
# ambience blips and a sound effect tail that are not speech.
REAL_TAKE_STDERR = """
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 1.069438 | silence_duration: 1.069438
[silencedetect @ 0x1] silence_start: 2.089062
[silencedetect @ 0x1] silence_end: 9.768625 | silence_duration: 7.679563
[silencedetect @ 0x1] silence_start: 9.785
[silencedetect @ 0x1] silence_end: 10.674313 | silence_duration: 0.889313
[silencedetect @ 0x1] silence_start: 10.834062
[silencedetect @ 0x1] silence_end: 12.360812 | silence_duration: 1.52675
[silencedetect @ 0x1] silence_start: 12.464937
[silencedetect @ 0x1] silence_end: 12.77575 | silence_duration: 0.310812
[silencedetect @ 0x1] silence_start: 13.07475
[silencedetect @ 0x1] silence_end: 13.531688 | silence_duration: 0.456937
"""


def test_episode_lines_reads_only_that_episode_in_order() -> None:
    spine = {
        "spine": {
            "beats": [
                {"episode_id": "episode_01", "dialogue_lines": [{"text": "I'm coming for you."}]},
                {"episode_id": "episode_02", "dialogue_lines": [{"text": "Not this one."}]},
                {"episode_id": "episode_01", "dialogue_lines": [{"text": " Second. "}, {"text": ""}]},
            ]
        }
    }
    assert episode_lines(spine, 1) == ["I'm coming for you.", "Second."]


def test_real_take_anchors_line_on_first_speech_span_not_the_tail() -> None:
    duration = 15.104
    spans = speech_spans(parse_silencedetect(REAL_TAKE_STDERR, duration), duration)
    assert spans[0] == Span(1.069438, 2.089062)
    [anchor] = anchor_lines(["I'm coming for you."], spans)
    assert anchor.start == pytest.approx(1.069, abs=0.01)
    assert anchor.end == pytest.approx(2.089, abs=0.01)


def test_short_blips_are_not_speech() -> None:
    spans = speech_spans([Span(0.0, 1.0), Span(1.05, 5.0)], 5.0)
    assert spans == []


def test_two_lines_take_consecutive_spans_and_absorb_short_pause() -> None:
    spans = [Span(0.5, 1.2), Span(1.4, 2.0), Span(4.0, 5.0)]
    anchors = anchor_lines(["I said I'm fine.", "Go."], spans)
    assert anchors == [Span(0.5, 2.0), Span(4.0, 5.0)]


def test_fewer_spans_than_lines_raises() -> None:
    with pytest.raises(ValueError, match="--line-start"):
        anchor_lines(["One.", "Two."], [Span(0.0, 1.0)])


def test_flicker_builds_three_words_then_resets() -> None:
    words = time_words("It's not coming. Mine's two", Span(0.0, 5.0))
    texts = [c.text for c in flicker_cues(words)]
    assert texts == ["It's", "It's not", "It's not coming.", "Mine's", "Mine's two"]


def test_flicker_events_touch_and_last_word_holds_until_next_line() -> None:
    cues = build_cues(["Go now.", "Run."], [Span(1.0, 2.0), Span(2.1, 2.6)])
    assert cues[0].end == cues[1].start
    assert cues[1].end == pytest.approx(2.1)
    assert cues[-1].text == "Run."


def test_ass_uses_house_style_scaled_to_frame() -> None:
    ass = build_ass([Cue(1.0, 1.5, "Hi")], width=768, height=1344)
    assert "Style: House,Poppins,50,&H0000E5FF,&H0000E5FF,&H00000000,&H80000000,-1," in ass
    assert ass.split("Style: House,")[1].split("\n")[0].endswith(",2,10,10,403,1")
    assert "Dialogue: 0,0:00:01.00,0:00:01.50,House,,0,0,0,,Hi" in ass
    half = build_ass([], width=384, height=672)
    assert "Style: House,Poppins,25," in half


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_caption_take_end_to_end(tmp_path: Path) -> None:
    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {"beats": [{"episode_id": "episode_01", "dialogue_lines": [{"text": "I'm coming for you."}]}]}
    (ep / "api" / "03_spine.json").write_text(json.dumps(spine))
    # Later approve receipt has no beats; the older full snapshot must still be used.
    (ep / "api" / "04_spine_approved.json").write_text(json.dumps({"approval_state": "approved"}))
    take = ep / "takes" / "take-ep01-t1-raw-v1.mp4"
    # 4 s clip: silence, a 1 s tone standing in for the line, silence.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=192x336:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4",
         "-filter_complex", "[1:a]volume='if(between(t,1,2),1,0)':eval=frame[a]",
         "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(take)],
        check=True,
    )
    result = caption_take(tmp_path)
    assert result.video.name == "take-ep01-t1-captioned-v1.mp4"
    assert result.ass.name == "take-ep01-t1-house-v1.ass"
    assert result.video.stat().st_size > 0
    assert result.anchors[0].start == pytest.approx(1.0, abs=0.1)
    assert result.anchors[0].end == pytest.approx(2.0, abs=0.1)
    assert "PlayResY: 336" in result.ass.read_text()
    again = caption_take(tmp_path)
    assert again.video.name == "take-ep01-t1-captioned-v2.mp4"
