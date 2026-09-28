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
    captions_whole_lines,
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


def test_episode_lines_find_a_later_episode_by_ordinal_not_by_name() -> None:
    spine = {
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}, {"episode_id": "ep_02", "ordinal": 2}],
        "beats": [
            {"episode_id": "episode_01", "dialogue_lines": [{"text": "First."}]},
            {"episode_id": "ep_02", "dialogue_lines": [{"text": "Second episode line."}]},
        ],
    }
    assert episode_lines(spine, 2) == ["Second episode line."]
    assert episode_lines(spine, 1) == ["First."]


def test_captions_on_a_japanese_show_are_the_english_subtitles() -> None:
    spine = {
        "spoken_language": "ja-JP",
        "beats": [{"episode_id": "episode_01", "dialogue_lines": [
            {"text": "Wait for me here.", "spoken_text": "ここで待ってて", "subtitle_text": "Wait here for me."}]}],
    }  # fmt: skip
    assert episode_lines(spine, 1) == ["Wait here for me."]


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


#: Word-flicker cues an English show got before whole-line captions existed (origin/main 2fba443).
ENGLISH_LINES = ["Go now, it is late.", "Run."]
ENGLISH_ANCHORS = [Span(1.0, 2.0), Span(2.5, 3.0)]
ENGLISH_CUES = [
    Cue(1.0, 1.15, "Go"),
    Cue(1.15, 1.4, "Go now,"),
    Cue(1.4, 1.55, "Go now, it"),
    Cue(1.55, 1.7, "is"),
    Cue(1.7, 2.15, "is late."),
    Cue(2.5, 3.15, "Run."),
]


def test_english_show_cues_are_unchanged_word_flicker() -> None:
    assert build_cues(ENGLISH_LINES, ENGLISH_ANCHORS) == ENGLISH_CUES
    assert build_cues(ENGLISH_LINES, ENGLISH_ANCHORS, whole_lines=False) == ENGLISH_CUES


def test_japanese_show_shows_each_whole_english_line_over_its_speech() -> None:
    cues = build_cues(ENGLISH_LINES, ENGLISH_ANCHORS, whole_lines=True)
    assert cues == [Cue(1.0, 2.15, "Go now, it is late."), Cue(2.5, 3.15, "Run.")]


def test_whole_line_hold_stops_at_the_next_line() -> None:
    cues = build_cues(["Wait here for me.", "No."], [Span(1.0, 2.0), Span(2.05, 2.6)], whole_lines=True)
    assert [c.text for c in cues] == ["Wait here for me.", "No."], "never split into words"
    assert cues[0].end == pytest.approx(2.05)


@pytest.mark.parametrize(
    ("spine", "whole"),
    [
        ({"spoken_language": "ja-JP"}, True),
        ({"spoken_language": "ko-KR"}, True),
        ({"spine": {"spoken_language": "ja"}}, True),
        ({"spoken_language": "en-US"}, False),
        ({"spoken_language": "EN"}, False),
        ({}, False),
    ],
)
def test_whole_lines_follow_the_spoken_language(spine: dict[str, object], whole: bool) -> None:
    assert captions_whole_lines(spine) is whole


def test_ass_uses_house_style_scaled_to_frame() -> None:
    ass = build_ass([Cue(1.0, 1.5, "Hi")], width=768, height=1344)
    assert "Style: House,Poppins,50,&H0000E5FF,&H0000E5FF,&H00000000,&H80000000,-1," in ass
    assert ass.split("Style: House,")[1].split("\n")[0].endswith(",2,10,10,511,1")  # bottom edge at 62% (social safe zones)
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


def _tone_take(path: Path) -> None:
    # 4 s clip: silence, a 1 s tone standing in for the line, silence.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=192x336:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4",
         "-filter_complex", "[1:a]volume='if(between(t,1,2),1,0)':eval=frame[a]",
         "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize(("language", "whole"), [("ja-JP", True), ("en-US", False)])
def test_caption_take_reads_the_spoken_language_from_the_spine(tmp_path: Path, language: str, whole: bool) -> None:
    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {
        "spoken_language": language,
        "beats": [{"episode_id": "episode_01", "dialogue_lines": [
            {"text": "Wait for me here, okay.", "spoken_text": "ここで待ってて", "subtitle_text": "Wait here for me, okay."}]}],
    }  # fmt: skip
    (ep / "api" / "03_spine.json").write_text(json.dumps(spine, ensure_ascii=False), encoding="utf-8")
    _tone_take(ep / "takes" / "take-ep01-t1-raw-v1.mp4")
    result = caption_take(tmp_path)
    assert result.whole_lines is whole
    if whole:
        assert [c.text for c in result.cues] == ["Wait here for me, okay."]
        assert "Wait here for me, okay." in result.ass.read_text(encoding="utf-8")
    else:
        assert [c.text for c in result.cues][:2] == ["Wait", "Wait here"]
