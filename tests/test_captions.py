"""Local house captions: line timing, flicker cues, ASS style."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import SPINE, make_take, make_tone, needs_ffmpeg

from creation.captions import (
    Cue,
    Span,
    anchor_lines,
    build_ass,
    build_cues,
    caption_take,
    captions_whole_lines,
    episode_caption_lines,
    episode_lines,
    fitted_size,
    flicker_cues,
    is_english,
    italic_size,
    parse_silencedetect,
    speech_spans,
    time_words,
)
from creation.cli_produce import main as produce_main
from creation.post.finish import run_finish

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
                {
                    "episode_id": "episode_01",
                    "dialogue_lines": [{"text": "I'm coming for you."}],
                },
                {
                    "episode_id": "episode_02",
                    "dialogue_lines": [{"text": "Not this one."}],
                },
                {
                    "episode_id": "episode_01",
                    "dialogue_lines": [{"text": " Second. "}, {"text": ""}],
                },
            ]
        }
    }
    assert episode_lines(spine, 1) == ["I'm coming for you.", "Second."]


def test_episode_lines_find_a_later_episode_by_ordinal_not_by_name() -> None:
    spine = {
        "episode_summaries": [
            {"episode_id": "episode_01", "ordinal": 1},
            {"episode_id": "ep_02", "ordinal": 2},
        ],
        "beats": [
            {"episode_id": "episode_01", "dialogue_lines": [{"text": "First."}]},
            {
                "episode_id": "ep_02",
                "dialogue_lines": [{"text": "Second episode line."}],
            },
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
    # Each whole line stays up long enough to read (max(1.2 s, 0.3 s a word)), extended forward
    # only and never into the next line: 5 words want 1.5 s but the next line starts at 2.5 s.
    assert cues == [Cue(1.0, 2.5, "Go now, it is late."), Cue(2.5, 3.7, "Run.")]


def test_whole_line_hold_stops_at_the_next_line() -> None:
    cues = build_cues(
        ["Wait here for me.", "No."],
        [Span(1.0, 2.0), Span(2.05, 2.6)],
        whole_lines=True,
    )
    assert [c.text for c in cues] == ["Wait here for me.", "No."], (
        "never split into words"
    )
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
def test_whole_lines_follow_the_spoken_language(
    spine: dict[str, object], whole: bool
) -> None:
    assert captions_whole_lines(spine) is whole


def test_ass_uses_house_style_scaled_to_frame() -> None:
    ass = build_ass([Cue(1.0, 1.5, "Hi")], width=768, height=1344)
    assert (
        "Style: House,Poppins,50,&H0000E5FF,&H0000E5FF,&H00000000,&H80000000,-1," in ass
    )
    assert (
        ass.split("Style: House,")[1].split("\n")[0].endswith(",2,10,10,511,1")
    )  # bottom edge at 62% (social safe zones)
    assert "Dialogue: 0,0:00:01.00,0:00:01.50,House,,0,0,0,,Hi" in ass
    half = build_ass([], width=384, height=672)
    assert "Style: House,Poppins,25," in half


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_caption_take_end_to_end(tmp_path: Path) -> None:
    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {
        "beats": [
            {
                "episode_id": "episode_01",
                "dialogue_lines": [{"text": "I'm coming for you."}],
            }
        ]
    }
    (ep / "api" / "03_spine.json").write_text(json.dumps(spine))
    # Later approve receipt has no beats; the older full snapshot must still be used.
    (ep / "api" / "04_spine_approved.json").write_text(
        json.dumps({"approval_state": "approved"})
    )
    take = ep / "takes" / "take-ep01-t1-raw-v1.mp4"
    # 4 s clip: silence, a 1 s tone standing in for the line, silence.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=192x336:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:d=4",
            "-filter_complex",
            "[1:a]volume='if(between(t,1,2),1,0)':eval=frame[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(take),
        ],
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
def test_caption_take_reads_the_spoken_language_from_the_spine(
    tmp_path: Path, language: str, whole: bool
) -> None:
    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {
        "spoken_language": language,
        "beats": [{"episode_id": "episode_01", "dialogue_lines": [
            {"text": "Wait for me here, okay.", "spoken_text": "ここで待ってて", "subtitle_text": "Wait here for me, okay."}]}],
    }  # fmt: skip
    (ep / "api" / "03_spine.json").write_text(
        json.dumps(spine, ensure_ascii=False), encoding="utf-8"
    )
    _tone_take(ep / "takes" / "take-ep01-t1-raw-v1.mp4")
    result = caption_take(tmp_path)
    assert result.whole_lines is whole
    if whole:
        assert [c.text for c in result.cues] == ["Wait here for me, okay."]
        assert "Wait here for me, okay." in result.ass.read_text(encoding="utf-8")
    else:
        assert [c.text for c in result.cues][:2] == ["Wait", "Wait here"]


# --- Heard-not-seen voices in Georgia italic; lines that are not English left uncaptioned ---

#: Four lines on one English show: an on-screen line (a Japanese line with an
#: English subtitle), a Japanese line with no English, an off-screen line, and
#: a voice-only cast member (the intercom) whose line has an English subtitle.
MIXED_SPINE = {
    "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
    "cast": [
        {"cast_id": "cast_aya", "name": "Aya"},
        {"cast_id": "cast_intercom", "name": "Intercom", "voice_only": True},
    ],
    "beats": [
        {"episode_id": "episode_01", "dialogue_lines": [
            {"line_id": "l1", "cast_id": "cast_aya", "text": "行こう", "subtitle_text": "Go now."},
            {"line_id": "l2", "cast_id": "cast_aya", "text": "待って"},
        ]},
        {"episode_id": "episode_01", "dialogue_lines": [
            {"line_id": "l3", "cast_id": "cast_aya", "text": "Behind you.", "off_screen": True},
            {"line_id": "l4", "cast_id": "cast_intercom", "text": "ドアを閉めて", "subtitle_text": "Close the door."},
        ]},
    ],
}  # fmt: skip

#: One tone per line, gaps long enough that each is its own speech span.
FOUR_TONES = ((0.4, 1.0, 440), (1.8, 2.4, 550), (3.2, 3.8, 660), (4.6, 5.2, 770))


def test_caption_lines_mark_off_screen_and_voice_only_lines_italic() -> None:
    lines = episode_caption_lines(MIXED_SPINE, 1)
    assert [(ln.line_id, ln.text, ln.italic) for ln in lines] == [
        ("l1", "Go now.", False),
        ("l2", "待って", False),
        ("l3", "Behind you.", True),
        ("l4", "Close the door.", True),
    ]
    assert [ln.english for ln in lines] == [True, False, True, True]
    assert episode_lines(MIXED_SPINE, 1) == [ln.text for ln in lines]


def test_off_screen_false_and_an_unflagged_cast_stay_upright() -> None:
    spine = {
        "cast": [{"cast_id": "cast_aya", "voice_only": False}, {"cast_id": "cast_ken"}],
        "beats": [{"episode_id": "episode_01", "dialogue_lines": [
            {"line_id": "a", "cast_id": "cast_aya", "text": "One.", "off_screen": False},
            {"line_id": "b", "cast_id": "cast_ken", "text": "Two."}]}],
    }  # fmt: skip
    assert [ln.italic for ln in episode_caption_lines(spine, 1)] == [False, False]


@pytest.mark.parametrize(
    ("text", "english"),
    [
        ("Wait here for me.", True),
        ("Café, naïve — “fine”, 100%!", True),
        ("ここで待ってて", False),
        ("정말 죄송합니다", False),
        ("你好", False),
        ("ｶﾀｶﾅ", False),  # half-width katakana
        ("ーー", False),  # prolonged sound mark
        ("Wait！", False),  # fullwidth punctuation: no glyph in the caption font
        ("「Wait」", False),
        ("Привет", False),
        ("नमस्ते", False),
        ("Hello Ελλάδα", False),
    ],
)
def test_is_english(text: str, english: bool) -> None:
    assert is_english(text) is english


def test_ass_sets_italic_cues_in_georgia_italic_and_the_rest_in_house() -> None:
    ass = build_ass(
        [Cue(1.0, 1.5, "Hi"), Cue(2.0, 2.5, "Behind", italic=True)],
        width=768,
        height=1344,
    )
    house = ass.split("Style: House,")[1].split("\n")[0].split(",")
    italic = ass.split("Style: Italic,")[1].split("\n")[0].split(",")
    assert italic[0] == "Georgia"
    # Bold off, Italic on; size, colours, spacing, edge, shadow, alignment and margins as House.
    assert (italic[6], italic[7]) == ("0", "-1")
    assert (house[6], house[7]) == ("-1", "0")
    # libass sizes a face by winAscent + winDescent: Georgia's Fontsize is scaled so both draw at the same em.
    assert (house[1], italic[1]) == ("50", "32") and italic[1] == str(italic_size(50))
    assert italic[2:6] == house[2:6] and italic[8:] == house[8:]
    assert "Dialogue: 0,0:00:01.00,0:00:01.50,House,,0,0,0,,Hi" in ass
    assert "Dialogue: 0,0:00:02.00,0:00:02.50,Italic,,0,0,0,,Behind" in ass


def test_a_long_italic_cue_is_fitted_at_the_italic_scale() -> None:
    long = "Close the door behind you before the lights go out tonight"
    fit = fitted_size(long, 50, 768)
    assert fit < 50
    ass = build_ass(
        [Cue(0.0, 1.0, long, italic=True), Cue(1.0, 2.0, long)], width=768, height=1344
    )
    assert f"Italic,,0,0,0,,{{\\fs{italic_size(fit)}}}{long}" in ass
    assert f"House,,0,0,0,,{{\\fs{fit}}}{long}" in ass


def test_build_cues_italic_and_skip_per_line() -> None:
    lines = ["Go now.", "待って", "Behind you."]
    anchors = [Span(0.4, 1.0), Span(1.8, 2.4), Span(3.2, 3.8)]
    cues = build_cues(
        lines, anchors, italic=[False, False, True], skip=[False, True, False]
    )
    assert [(c.text, c.italic) for c in cues] == [
        ("Go", False), ("Go now.", False), ("Behind", True), ("Behind you.", True)
    ]  # fmt: skip
    assert all(not (1.8 <= c.start < 2.4) for c in cues), (
        "the skipped line draws nothing"
    )
    assert cues[1].end <= 1.8, "the line before a skipped line still stops at its start"
    whole = build_cues(
        lines,
        anchors,
        whole_lines=True,
        italic=[False, False, True],
        skip=[False, True, False],
    )
    assert [(c.text, c.italic) for c in whole] == [
        ("Go now.", False),
        ("Behind you.", True),
    ]


def _mixed_desk(desk: Path) -> Path:
    ep = desk / "ep01"
    (ep / "api").mkdir(parents=True, exist_ok=True)
    (ep / "api" / "03_spine.json").write_text(
        json.dumps(MIXED_SPINE, ensure_ascii=False), encoding="utf-8"
    )
    return make_take(
        ep / "takes" / "take-ep01-t1-raw-v1.mp4", seconds=6.0, tones=FOUR_TONES
    )


def _dialogue(ass_text: str) -> list[tuple[str, str]]:
    return [
        (row.split(",")[3], row.split(",,", 2)[-1].split(",,")[-1])
        for row in ass_text.splitlines()
        if row.startswith("Dialogue:")
    ]


@needs_ffmpeg
def test_caption_take_italic_for_heard_voices_and_skips_lines_that_are_not_english(
    tmp_path: Path,
) -> None:
    _mixed_desk(tmp_path)
    result = caption_take(tmp_path)
    assert result.italic == (False, False, True, True)
    assert result.not_english == (
        'NOT ENGLISH: l2 "待って" — add an English subtitle with `edit`/`line --subtitle`',
    )
    # The skipped line still takes its speech span: the lines after it stay on their own tone.
    assert [round(a.start, 1) for a in result.anchors] == [0.4, 1.8, 3.2, 4.6]
    rows = _dialogue(result.ass.read_text(encoding="utf-8"))
    assert {text for style, text in rows if style == "House"} == {"Go", "Go now."}
    assert {text for style, text in rows if style == "Italic"} == {
        "Behind", "Behind you.", "Close", "Close the", "Close the door."
    }  # fmt: skip
    assert not any("待" in text or "ドア" in text for _, text in rows)
    assert result.video.stat().st_size > 0


@needs_ffmpeg
def test_caption_command_prints_not_english(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _mixed_desk(post_desk)
    assert produce_main(["caption", "--desk", str(post_desk), "--no-open"]) == 0
    out = capsys.readouterr().out
    assert 'NOT ENGLISH: l2 "待って" — add an English subtitle' in out
    assert "Behind you.  (italic: heard, not seen)" in out
    assert "NOT ENGLISH: l2" in (post_desk / "ep01" / "run-notes.md").read_text(
        encoding="utf-8"
    )


@needs_ffmpeg
def test_finish_summary_shows_not_english(post_desk: Path) -> None:
    spine = json.loads(json.dumps(SPINE))
    lines = spine["beats"][0]["dialogue_lines"]
    lines[0]["off_screen"] = True
    lines[1]["text"] = "今夜はだめ"
    (post_desk / "ep01" / "api" / "03_spine.json").write_text(
        json.dumps(spine, ensure_ascii=False), encoding="utf-8"
    )
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
              tones=((1.0, 2.0, 440), (3.2, 4.0, 880)))  # fmt: skip
    out = io.StringIO()
    result = run_finish(
        post_desk,
        sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
        bed_maker=lambda spine, music, target: make_tone(
            target.with_suffix(".wav"), seconds=6.0, freq=220
        ),
        facts_fetcher=lambda *a: None,
        stream=out,
    )
    captions = next(s for s in result.steps if s.step == "captions")
    assert captions.status == "ran"
    warning = 'NOT ENGLISH: l2 "今夜はだめ" — add an English subtitle with `edit`/`line --subtitle`'
    assert warning in captions.detail
    assert "- captions: ran — " in out.getvalue() and warning in out.getvalue()
    ass = next((post_desk / "ep01" / "takes").glob("take-ep01-t1-cap-v*.ass"))
    rows = _dialogue(ass.read_text(encoding="utf-8"))
    assert rows and all(style == "Italic" for style, _ in rows), "Kenji is off screen"
    assert not any("今夜" in text for _, text in rows)


@needs_ffmpeg
def test_caption_command_warns_when_georgia_italic_falls_back(
    post_desk: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import creation.captions as cap

    monkeypatch.setattr(
        cap,
        "find_italic_font",
        lambda: cap.ItalicFont(None, None, "Arial (/a/Arial.ttf)"),
    )
    _mixed_desk(post_desk)
    assert produce_main(["caption", "--desk", str(post_desk), "--no-open"]) == 0
    err = capsys.readouterr().err
    assert "WARNING FONT: Georgia not found" in err and "Arial (/a/Arial.ttf)" in err
    notes = (post_desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert "FONT: Georgia not found" in notes
