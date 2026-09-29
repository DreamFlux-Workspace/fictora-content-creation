"""Whole English lines timed on the words a transcript heard, not on speech spans.

The fixture is a real take (Hanakaze Sweets ep02 t1, 2026-09-29, spoken in
Japanese, English captions). Speech-span timing put line 2 on a stammer at
9.47 s and ended line 1 at a mid-line pause (captions 0.03-1.63 s and
9.47-10.43 s); the dialogue runs 0.03-3.05 s and 11.33-13.25 s.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from conftest import make_take, make_tone, needs_ffmpeg

from creation.captions import (
    CaptionLine,
    Cue,
    Span,
    anchor_lines,
    build_cues,
    caption_take,
    episode_caption_lines,
    speech_spans,
    time_lines,
    word_span,
)
from creation.cli_produce import main as produce_main
from creation.post.finish import run_finish
from creation.post.whisper import Word, load_words

#: ``take-ep02-t1-review-words-v1.json`` as the server returned it (``/v1/transcripts``).
HANAKAZE_WORDS = {
    "schema_version": "fictora.drama-transcript-response.v1",
    "language": "ja",
    "words": [
        {"word": "お", "start": 0.03, "end": 0.23, "reading": "オ"},
        {"word": "餅", "start": 0.23, "end": 0.53, "reading": "モチ"},
        {"word": "無", "start": 0.53, "end": 1.01, "reading": "ム"},
        {"word": "料", "start": 1.01, "end": 1.25, "reading": "リョウ"},
        {"word": "です", "start": 1.25, "end": 1.51, "reading": "デス"},
        {"word": "こちら", "start": 1.51, "end": 2.43, "reading": "コチラ"},
        {"word": "へ", "start": 2.43, "end": 2.67, "reading": "ヘ"},
        {"word": "どう", "start": 2.67, "end": 2.83, "reading": "ドウ"},
        {"word": "ぞ", "start": 2.83, "end": 3.05, "reading": "ゾ"},
        {"word": "ん", "start": 7.63, "end": 9.47, "reading": "ン"},
        {"word": "もう", "start": 9.47, "end": 11.33, "reading": "モウ"},
        {"word": "申", "start": 11.33, "end": 12.09, "reading": "サル"},
        {"word": "し", "start": 12.09, "end": 12.23, "reading": "シ"},
        {"word": "訳", "start": 12.23, "end": 12.35, "reading": "ワケ"},
        {"word": "ござ", "start": 12.35, "end": 12.59, "reading": "ゴザ"},
        {"word": "い", "start": 12.59, "end": 12.73, "reading": "イ"},
        {"word": "ません", "start": 12.73, "end": 12.93, "reading": "マセン"},
        {"word": "でした", "start": 12.93, "end": 13.25, "reading": "デシタ"},
        {"word": "数", "start": 13.25, "end": 13.51, "reading": "スウ"},
    ],
}  # fmt: skip

#: The two lines of that take as the spine has them.
HANAKAZE_SPINE = {
    "spoken_language": "ja-JP",
    "episode_summaries": [{"episode_id": "episode_02", "ordinal": 2}],
    "beats": [{"episode_id": "episode_02", "dialogue_lines": [
        {"line_id": "line_episode_02_01", "text": "Free mochi! This way!",
         "spoken_text": "お餅、無料でーす！こちらへどうぞ！", "subtitle_text": "Free mochi! Right this way!"},
        {"line_id": "line_episode_02_03", "text": "I’m sorry—my comeback is cancelled!",
         "spoken_text": "も、申し訳ございませんでしたっ！！", "subtitle_text": "I-I'm terribly sorry!!"},
    ]}],
}  # fmt: skip

#: ffmpeg silencedetect on the same take: stammer, two fragments, then the line.
HANAKAZE_SPEECH = [Span(0.03, 1.63), Span(9.50, 9.60), Span(10.25, 10.50),
                   Span(11.15, 11.48), Span(11.93, 14.78)]  # fmt: skip


def _words() -> tuple[Word, ...]:
    return tuple(
        Word(w["start"], w["end"], w["word"], w["reading"])
        for w in HANAKAZE_WORDS["words"]
    )


def _lines() -> list[CaptionLine]:
    return episode_caption_lines(HANAKAZE_SPINE, 2)


def _no_spans() -> list[Span]:
    raise AssertionError("every line is matched: speech spans must not be asked for")


def test_hanakaze_lines_follow_the_words_not_the_stammer_or_the_pause() -> None:
    timing = time_lines(_lines(), duration=15.0, words=_words(), spans=_no_spans)
    one, two = timing.anchors
    assert timing.methods == ("words", "words")
    assert one.start == pytest.approx(0.03) and one.end >= 3.05
    assert two.start == pytest.approx(11.33, abs=0.05), (
        "not 9.47: 'もう' lasts 1.86 s (a stammer Whisper drew out to the next word)"
    )
    assert two.end >= 13.25
    cues = build_cues(
        [line.text for line in _lines()], list(timing.anchors), whole_lines=True,
        holds=timing.holds, fixed_ends=timing.fixed_ends,
    )  # fmt: skip
    assert cues == [
        Cue(0.03, 3.3, "Free mochi! Right this way!"),
        Cue(11.33, 13.5, "I-I'm terribly sorry!!"),
    ]


def test_speech_spans_put_the_same_take_on_the_stammer() -> None:
    """The old method on the measured spans: why this change exists (the v2 captions)."""

    texts = [line.text for line in _lines()]
    anchors = anchor_lines(texts, HANAKAZE_SPEECH)
    assert anchors[0].end == pytest.approx(1.63)
    assert anchors[1].start == pytest.approx(9.50)


def test_a_line_the_transcript_did_not_hear_falls_back_to_speech_spans() -> None:
    words = tuple(
        w for w in _words() if w.start < 5.0
    )  # line 2 is not in the transcript
    asked: list[bool] = []

    def spans() -> list[Span]:
        asked.append(True)
        return HANAKAZE_SPEECH

    timing = time_lines(_lines(), duration=15.0, words=words, spans=spans)
    assert asked == [True]
    assert timing.methods == ("words", "speech")
    assert timing.anchors[0] == Span(0.03, 3.05)
    assert timing.anchors[1].start == pytest.approx(9.50)
    assert timing.holds == (0.25, 0.15)


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        # A stretched first word is skipped: the line starts on the next word.
        ([(9.47, 11.33, "もう", "モウ"), (11.33, 12.09, "申", "サル"), (12.09, 13.25, "し", "シ")],
         Span(11.33, 13.25)),
        # A short stammer cut off by a pause over 0.6 s is skipped.
        ([(9.5, 9.6, "も", "モ"), (11.2, 12.0, "申し", "モウシ"), (12.0, 13.0, "訳", "ワケ")],
         Span(11.2, 13.0)),
        # A pause after a real (longer) first word is part of the line: the start stays.
        ([(1.0, 1.4, "Listen.", None), (2.4, 2.8, "Please.", None)], Span(1.0, 2.8)),
        # A stretched last word is cut to what one word can last.
        ([(1.0, 1.3, "Go", None), (1.3, 4.0, "now", None)], Span(1.0, 2.5)),
        # A single stretched word keeps what one word can last, up to its end.
        ([(3.0, 6.0, "ん", "ン")], Span(4.8, 6.0)),
    ],
)  # fmt: skip
def test_word_span_trims_stammers_and_stretched_words(
    words: list[tuple[float, float, str, str | None]], expected: Span
) -> None:
    assert word_span([Word(*w) for w in words]) == expected


def test_a_whole_line_stays_up_long_enough_to_read_but_never_into_the_next() -> None:
    lines = ["No.", "I told you already, not tonight."]
    cues = build_cues(lines, [Span(1.0, 1.3), Span(2.0, 3.0)], whole_lines=True)
    assert cues[0] == Cue(1.0, 2.0, "No."), "wants 1.2 s, stops at the next line"
    assert cues[1] == Cue(2.0, 3.8, lines[1]), "6 words: 1.8 s"


def test_line_end_overrides_the_end_exactly() -> None:
    timing = time_lines(
        _lines(), duration=15.0, words=_words(), line_ends=[2.0, 14.0], spans=_no_spans
    )
    assert timing.anchors == (Span(0.03, 2.0), Span(11.33, 14.0))
    assert timing.methods == ("words start, manual end", "words start, manual end")
    cues = build_cues(
        [line.text for line in _lines()], list(timing.anchors), whole_lines=True,
        holds=timing.holds, fixed_ends=timing.fixed_ends,
    )  # fmt: skip
    assert [(c.start, c.end) for c in cues] == [(0.03, 2.0), (11.33, 14.0)], (
        "a hand end has no hold and no readable minimum"
    )


def test_line_start_and_end_by_hand() -> None:
    timing = time_lines(
        _lines(), duration=15.0, line_starts=[0.5, 11.0], line_ends=[3.2, 13.6]
    )
    assert timing.anchors == (Span(0.5, 3.2), Span(11.0, 13.6))
    assert timing.methods == ("manual", "manual")


def test_line_start_alone_keeps_the_word_end_when_there_is_one() -> None:
    timing = time_lines(
        _lines(), duration=15.0, words=_words(), line_starts=[0.0, 11.2]
    )
    assert timing.anchors == (Span(0.0, 3.05), Span(11.2, 13.25))
    assert timing.methods == ("manual start, words end",) * 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"line_ends": [3.0]}, "--line-end given 1 time"),
        ({"line_starts": [1.0]}, "--line-start given 1 time"),
        ({"line_starts": [1.0, 5.0], "line_ends": [0.5, 6.0]}, "line 1 ends at 0.50s"),
    ],
)
def test_hand_times_are_checked(kwargs: dict[str, list[float]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        time_lines(_lines(), duration=15.0, spans=lambda: HANAKAZE_SPEECH, **kwargs)


# --- English shows: unchanged -------------------------------------------------------------------------


def test_english_show_timing_is_speech_spans_exactly_as_before() -> None:
    lines = [CaptionLine("l1", "I said I'm fine."), CaptionLine("l2", "Go.")]
    spans = [Span(0.5, 1.2), Span(1.4, 2.0), Span(4.0, 5.0)]
    timing = time_lines(lines, duration=6.0, spans=lambda: spans)
    assert list(timing.anchors) == anchor_lines([line.text for line in lines], spans)
    assert timing.methods == ("speech", "speech")
    assert timing.holds == (0.15, 0.15) and timing.fixed_ends == (False, False)
    # Hand starts: the old estimated end (line length, up to the next start).
    hand = time_lines(lines, duration=6.0, line_starts=[0.5, 1.0])
    assert hand.anchors == (Span(0.5, 1.0), Span(1.0, 1.6))
    assert hand.methods == ("manual start, estimated end",) * 2


def _english_desk(root: Path) -> None:
    spine = {
        "beats": [{"episode_id": "episode_01", "dialogue_lines": [
            {"line_id": "l1", "text": "Wait for me here."},
            {"line_id": "l2", "text": "Not tonight."},
        ]}],
    }  # fmt: skip
    (root / "ep01" / "api").mkdir(parents=True)
    (root / "ep01" / "api" / "03_spine.json").write_text(json.dumps(spine))
    make_take(root / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
              tones=((1.0, 2.0, 440), (3.2, 4.0, 880)))  # fmt: skip


@needs_ffmpeg
def test_english_show_ignores_a_transcript(tmp_path: Path) -> None:
    """A transcript never changes an English show's captions: word flicker on speech spans."""

    _english_desk(tmp_path)
    words = tmp_path / "words.json"
    # Words that would move both lines if they were used.
    words.write_text(json.dumps({"words": [
        {"word": "Wait", "start": 0.2, "end": 0.4}, {"word": "for", "start": 0.4, "end": 0.5},
        {"word": "me", "start": 0.5, "end": 0.6}, {"word": "here", "start": 0.6, "end": 0.8},
        {"word": "Not", "start": 2.5, "end": 2.7}, {"word": "tonight", "start": 2.7, "end": 3.0},
    ]}))  # fmt: skip
    plain = caption_take(tmp_path)
    with_words = caption_take(tmp_path, words_json=words)
    assert with_words.cues == plain.cues
    assert with_words.anchors == plain.anchors
    assert with_words.methods == ("speech", "speech") and with_words.words_json is None
    assert plain.cues[0].text == "Wait" and plain.anchors[1].start == pytest.approx(
        3.2, abs=0.1
    )


# --- caption and finish on a Japanese show --------------------------------------------------------------


def _japanese_desk(root: Path) -> Path:
    """A ja show: line 1 at 1.0-1.9 s, a stammer 'も、' at 2.6-2.8 s, then line 2 at 3.5-4.2 s."""

    spine = {
        "spoken_language": "ja-JP",
        "beats": [{"episode_id": "episode_01", "dialogue_lines": [
            {"line_id": "l1", "text": "Wait here.", "spoken_text": "ここで待ってて", "subtitle_text": "Wait here for me."},
            {"line_id": "l2", "text": "Sorry.", "spoken_text": "も、申し訳ない", "subtitle_text": "I-I'm sorry."},
        ]}],
    }  # fmt: skip
    api = root / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "03_spine.json").write_text(
        json.dumps(spine, ensure_ascii=False), encoding="utf-8"
    )
    make_take(root / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", seconds=6.0,
              tones=((1.0, 1.9, 440), (2.6, 2.8, 500), (3.5, 4.2, 880)))  # fmt: skip
    return root


def _japanese_words(path: Path) -> Path:
    path.write_text(json.dumps({"words": [
        {"word": "ここ", "start": 1.0, "end": 1.3, "reading": "ココ"},
        {"word": "で", "start": 1.3, "end": 1.4, "reading": "デ"},
        {"word": "待", "start": 1.4, "end": 1.6, "reading": "マ"},
        {"word": "って", "start": 1.6, "end": 1.75, "reading": "ッテ"},
        {"word": "て", "start": 1.75, "end": 1.9, "reading": "テ"},
        {"word": "も", "start": 2.6, "end": 2.8, "reading": "モ"},
        {"word": "申", "start": 3.5, "end": 3.7, "reading": "モウ"},
        {"word": "し", "start": 3.7, "end": 3.8, "reading": "シ"},
        {"word": "訳", "start": 3.8, "end": 4.0, "reading": "ワケ"},
        {"word": "ない", "start": 4.0, "end": 4.2, "reading": "ナイ"},
    ]}, ensure_ascii=False), encoding="utf-8")  # fmt: skip
    return path


@needs_ffmpeg
def test_caption_command_times_whole_lines_on_the_saved_transcript(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _japanese_desk(post_desk)
    _japanese_words(post_desk / "ep01" / "takes" / "take-ep01-t1-review-words-v1.json")
    assert produce_main(["caption", "--desk", str(post_desk), "--no-open"]) == 0
    out = capsys.readouterr().out
    assert "3.50-" in out and "I-I'm sorry.  [words]" in out, out
    notes = (post_desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert '"I-I\'m sorry." (words)' in notes


@needs_ffmpeg
def test_caption_command_line_end(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _japanese_desk(post_desk)
    assert produce_main(["caption", "--desk", str(post_desk), "--no-open",
                         "--line-end", "2.2", "--line-end", "5.5"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "2.20s  Wait here for me.  [speech start, manual end]" in out, out
    assert "5.50s  I-I'm sorry.  [speech start, manual end]" in out, out


def _finish(desk: Path, **kwargs: object) -> tuple[object, str]:
    out = io.StringIO()
    result = run_finish(
        desk,
        sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
        bed_maker=lambda spine, music, target: make_tone(
            target.with_suffix(".wav"), seconds=6.0, freq=220
        ),
        facts_fetcher=lambda *a: None,
        stream=out,
        **kwargs,  # type: ignore[arg-type]
    )
    return result, out.getvalue()


@needs_ffmpeg
def test_finish_makes_a_transcript_and_times_whole_lines_on_it(post_desk: Path) -> None:
    _japanese_desk(post_desk)
    asked: list[tuple[int, str]] = []

    def transcriber(desk: Path, episode: int, take_id: str) -> Path:
        asked.append((episode, take_id))
        return _japanese_words(
            desk / "ep01" / "takes" / "take-ep01-t1-review-words-v1.json"
        )

    result, out = _finish(post_desk, transcriber=transcriber)
    assert asked == [(1, "t1")]
    captions = next(s for s in result.steps if s.step == "captions")  # type: ignore[attr-defined]
    assert captions.status == "ran", out
    assert (
        "transcript made on the server: `take-ep01-t1-review-words-v1.json`"
        in captions.detail
    )
    # Line 2 starts on its words, not on the stammer at 2.6 s; each line is held to be read (1.2 s).
    assert '3.50-4.70s "I-I\'m sorry." (words)' in captions.detail, captions.detail
    assert "1.00-2.20s 'Wait here for me.' (words)" in captions.detail, captions.detail
    # A saved transcript is reused: no second call.
    _finish(post_desk, transcriber=transcriber)
    assert asked == [(1, "t1")]


@needs_ffmpeg
def test_finish_falls_back_to_speech_when_no_transcript_can_be_made(
    post_desk: Path,
) -> None:
    _japanese_desk(post_desk)

    def transcriber(desk: Path, episode: int, take_id: str) -> Path:
        raise ValueError("no stored URL for ep01 t1")

    result, out = _finish(post_desk, transcriber=transcriber)
    captions = next(s for s in result.steps if s.step == "captions")  # type: ignore[attr-defined]
    assert "no transcript (ValueError: no stored URL for ep01 t1)" in captions.detail
    assert '"I-I\'m sorry." (speech)' in captions.detail
    assert "2.6" in captions.detail.split("'Wait here for me.' (speech); ")[1][:4], (
        "speech spans still start line 2 on the stammer tone"
    )


@needs_ffmpeg
def test_finish_line_end(post_desk: Path) -> None:
    _japanese_desk(post_desk)
    _japanese_words(post_desk / "ep01" / "takes" / "take-ep01-t1-review-words-v1.json")
    result, _ = _finish(post_desk, line_ends=(2.1, 5.0))
    captions = next(s for s in result.steps if s.step == "captions")  # type: ignore[attr-defined]
    assert "1.00-2.10s 'Wait here for me.' (words start, manual end)" in captions.detail
    assert '3.50-5.00s "I-I\'m sorry." (words start, manual end)' in captions.detail


def test_speech_spans_helper_still_used_for_the_fallback() -> None:
    assert speech_spans([Span(0.0, 1.0), Span(2.0, 3.0)], 3.0) == [Span(1.0, 2.0)]


def test_load_words_reads_the_saved_hanakaze_transcript(tmp_path: Path) -> None:
    path = tmp_path / "w.json"
    path.write_text(json.dumps(HANAKAZE_WORDS, ensure_ascii=False), encoding="utf-8")
    assert load_words(path) == _words()
