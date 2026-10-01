"""Captions sit on the right words and the right lines.

- L-20260930-6: on an English show captions are anchored on speech stretches in
  order. Extra speech (a stray mumble, a line the take invented) pushed every
  later line onto the wrong words ("You must be Hana." over Hana) with no warning.
  Now: a loud ``EXTRA SPEECH`` warning, and a saved transcript times English
  lines too. ``--line-start`` / ``--line-end`` still win; a locked-voice take
  (``target_audio``) is still timed on its exact ``soundtrack.lines``.
- L-20261001-8 (Sighted ep 1 desk 2): finish burned a caption for a line
  deleted before filming and left narration laid with ``finish --voice``
  uncaptioned. Now finish captions from the current spine (read from the server)
  and captions a ``--voice`` line that is not a script line in the heard-not-seen
  style.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from conftest import SPINE, make_take, make_tone, needs_ffmpeg

from creation.captions import (
    CaptionLine,
    Span,
    caption_take,
    extra_speech_warning,
    time_lines,
    without_windows,
)
from creation.post.finish import run_finish
from creation.post.hand import Placed
from test_post_finish import FACTS, fake_bed, fake_sfx

LINES = [CaptionLine("l1", "Wait for me here."), CaptionLine("l2", "Not tonight.")]

#: A stray mumble at 0.2-0.5 s, then Kenji at 1.2-2.0 s and Aya at 3.2-4.0 s.
STRAY_FIRST = ((0.2, 0.5, 300), (1.2, 2.0, 440), (3.2, 4.0, 880))


def _words(path: Path, spans: list[tuple[str, float, float]]) -> Path:
    words = []
    for text, start, end in spans:
        parts = text.split()
        step = (end - start) / len(parts)
        words += [
            {"word": w, "start": start + i * step, "end": start + (i + 1) * step}
            for i, w in enumerate(parts)
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"words": words}))
    return path


HEARD = [("um", 0.2, 0.5), ("Wait for me here.", 1.2, 2.0), ("Not tonight.", 3.2, 4.0)]


# --- (a) extra speech is loud --------------------------------------------------------------------------


def test_more_speech_stretches_than_lines_warns_and_names_the_unused_stretches() -> (
    None
):
    spans = [Span(0.2, 0.5), Span(1.2, 2.0), Span(3.2, 4.0)]
    timing = time_lines(LINES, duration=5.0, spans=lambda: spans)
    # Still anchored in order (the stray takes line 1): that is exactly why the warning matters.
    assert timing.anchors[0] == Span(0.2, 0.5)
    assert len(timing.warnings) == 1
    warning = timing.warnings[0]
    assert warning.startswith("EXTRA SPEECH: 3 speech stretch(es) for 2 line(s)")
    assert "3.20-4.00s" in warning and "--line-start" in warning


def test_one_stretch_per_line_is_quiet() -> None:
    timing = time_lines(
        LINES, duration=5.0, spans=lambda: [Span(1.2, 2.0), Span(3.2, 4.0)]
    )
    assert timing.warnings == ()


def test_a_pause_inside_a_line_is_not_extra_speech() -> None:
    # Kenji pauses 0.3 s mid-line: two stretches, one line; absorbed, nothing left over.
    timing = time_lines(
        LINES,
        duration=5.0,
        spans=lambda: [Span(1.2, 1.6), Span(1.9, 2.3), Span(3.2, 4.0)],
    )
    assert timing.anchors == (Span(1.2, 2.3), Span(3.2, 4.0))
    assert timing.warnings == ()


def test_no_warning_when_no_line_was_timed_on_speech_stretches() -> None:
    timing = time_lines(
        LINES,
        duration=5.0,
        line_starts=[1.2, 3.2],
        spans=lambda: [Span(0.2, 0.5), Span(1.2, 2.0), Span(3.2, 4.0)],
    )
    assert timing.methods[0].startswith("manual") and timing.warnings == ()


def test_extra_speech_warning_lists_at_most_five_stretches() -> None:
    unused = [Span(float(i), i + 0.5) for i in range(8)]
    warning = extra_speech_warning(line_count=1, span_count=9, unused=unused)
    assert "0.00-0.50s" in warning and "4.00-4.50s" in warning
    assert "5.00-5.50s" not in warning and "and 3 more" in warning


def test_laid_windows_are_left_out_of_speech_stretches() -> None:
    spans = [Span(0.2, 0.5), Span(1.2, 2.0), Span(3.2, 4.0)]
    assert without_windows(spans, [(0.1, 0.6)]) == [Span(1.2, 2.0), Span(3.2, 4.0)]
    assert without_windows(spans, [(1.5, 1.6)]) == [
        Span(0.2, 0.5),
        Span(1.2, 1.5),
        Span(1.6, 2.0),
        Span(3.2, 4.0),
    ]


def _english_desk(root: Path, tones: tuple[tuple[float, float, int], ...]) -> Path:
    api = root / "ep01" / "api"
    api.mkdir(parents=True)
    (api / "spine.json").write_text(json.dumps(SPINE))
    make_take(root / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=tones)
    return root


@needs_ffmpeg
def test_caption_take_says_extra_speech_on_an_english_take(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = caption_take(_english_desk(tmp_path, STRAY_FIRST))
    assert result.timing_warning.startswith("EXTRA SPEECH")
    assert "WARNING EXTRA SPEECH" in capsys.readouterr().err


# --- (b) a transcript times English lines too -------------------------------------------------------------


@needs_ffmpeg
def test_caption_take_times_english_lines_on_a_transcript_when_one_is_given(
    tmp_path: Path,
) -> None:
    desk = _english_desk(tmp_path, STRAY_FIRST)
    words = _words(desk / "ep01" / "takes" / "take-ep01-t1-review-words-v1.json", HEARD)
    result = caption_take(desk, words_json=words)
    assert result.methods == ("words", "words")
    assert result.anchors[0].start == pytest.approx(1.2, abs=0.01)
    assert result.anchors[1].start == pytest.approx(3.2, abs=0.01)
    assert result.timing_warning == ""
    assert not result.whole_lines, "an English show still flickers word by word"


@needs_ffmpeg
def test_hand_times_still_override_the_transcript(tmp_path: Path) -> None:
    desk = _english_desk(tmp_path, STRAY_FIRST)
    words = _words(desk / "ep01" / "takes" / "take-ep01-t1-review-words-v1.json", HEARD)
    result = caption_take(desk, words_json=words, line_starts=[1.0, 3.0])
    assert [a.start for a in result.anchors] == [1.0, 3.0]
    assert result.methods[0].startswith("manual start")


@needs_ffmpeg
def test_finish_times_an_english_take_on_its_saved_transcript(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", tones=STRAY_FIRST)
    _words(takes / "take-ep01-t1-review-words-v1.json", HEARD)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )

    def no_server_transcript(*_: object) -> Path:
        raise AssertionError("an English take is never sent for a transcript")

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, transcriber=no_server_transcript,
                        thumbnail=False, stream=io.StringIO())  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert "'Wait for me here.' (words)" in detail and "1.20-" in detail, detail
    assert "3.20-" in detail and "'Not tonight.' (words)" in detail, detail
    assert "EXTRA SPEECH" not in detail


@needs_ffmpeg
def test_finish_without_a_transcript_warns_about_extra_speech(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", tones=STRAY_FIRST)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, thumbnail=False,
                        stream=io.StringIO())  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert "EXTRA SPEECH" in detail, detail
    notes = (post_desk / "ep01" / "run-notes.md").read_text()
    assert "EXTRA SPEECH" in notes


@needs_ffmpeg
def test_a_locked_voice_take_stays_on_its_soundtrack_lines_even_with_a_transcript(
    post_desk: Path,
) -> None:
    from test_target_audio_soundtrack import TARGET, _desk_take, facts_with

    _desk_take(post_desk, facts_with(TARGET))
    # A transcript that would put both lines a second late: the dialogue track's windows are exact.
    _words(
        post_desk / "ep01" / "takes" / "take-ep01-t1-review-words-v1.json",
        [("Wait for me here.", 2.0, 3.0), ("Not tonight.", 4.2, 4.8)],
    )
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, cut_meter=lambda _take: (2.5,),
                        thumbnail=False, stream=io.StringIO())  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert "1.00-" in detail and "3.20-" in detail and "(lines)" in detail, detail
    assert "(words)" not in detail


# --- L-20261001-8: the current spine, and --voice lines ---------------------------------------------------


def _current_spine_without_l2() -> dict:
    spine = copy.deepcopy(SPINE)
    spine["beats"][0]["dialogue_lines"] = spine["beats"][0]["dialogue_lines"][:1]
    return spine


@needs_ffmpeg
def test_finish_captions_from_the_current_spine_not_a_stale_desk_copy(
    post_desk: Path,
) -> None:
    # The desk's copy still has Aya's l2; it was deleted on the server before filming.
    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", tones=((1.2, 2.0, 440),))
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    current = _current_spine_without_l2()
    asked: list[tuple[Path, int]] = []

    def fetch(desk: Path, episode: int) -> dict:
        asked.append((desk, episode))
        return current

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, spine_fetcher=fetch,
                        thumbnail=False, stream=io.StringIO())  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert asked == [(post_desk, 1)]
    assert "Not tonight." not in detail, detail
    assert detail.startswith("1 line(s)"), detail


@needs_ffmpeg
def test_finish_says_so_when_the_current_spine_cannot_be_read(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(
        takes / "take-ep01-t1-raw-v1.mp4", tones=((1.2, 2.0, 440), (3.2, 4.0, 880))
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )

    def offline(desk: Path, episode: int) -> dict:
        raise OSError("no network")

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, spine_fetcher=offline,
                        thumbnail=False, stream=io.StringIO())  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert "!! captions from the desk's copy `03_spine.json`" in detail, detail
    assert "no network" in detail


@needs_ffmpeg
def test_finish_captions_narration_laid_with_voice_in_the_heard_not_seen_style(
    post_desk: Path,
) -> None:
    # Sighted ep 1: the narrator's lines were removed from the spine and laid with finish --voice.
    takes = post_desk / "ep01" / "takes"
    make_take(
        takes / "take-ep01-t1-raw-v1.mp4", tones=((1.2, 2.0, 440), (3.2, 4.0, 880))
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    voices = post_desk / "ep01" / "voices"
    narration = make_tone(voices / "voice-ep01-narrator-v1.wav", seconds=0.6, freq=550)
    narration.with_suffix(".json").write_text(
        json.dumps({"line": "The city never sleeps."})
    )
    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, voices=(Placed(narration, 0.2),),
                        thumbnail=False, stream=out)  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert "'The city never sleeps.' (laid)" in detail, detail
    assert "0.20-" in detail
    # The narration is left out of the speech stretches: the script lines stay on their own speech.
    assert "1.2" in detail.split("'Wait for me here.'")[0][-12:], detail
    assert "3.2" in detail.split("'Not tonight.'")[0][-12:], detail
    assert "EXTRA SPEECH" not in detail, detail
    ass = next(takes.glob("take-ep01-t1-cap-v*.ass")).read_text()
    assert any(
        row.startswith("Dialogue:") and ",Italic," in row and "The" in row
        for row in ass.splitlines()
    ), ass


@needs_ffmpeg
def test_a_voice_line_that_is_a_script_line_is_captioned_once(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", tones=((1.2, 2.0, 440),))
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    voices = post_desk / "ep01" / "voices"
    redo = make_tone(voices / "voice-ep01-aya-v1.wav", seconds=0.8, freq=660)
    redo.with_suffix(".json").write_text(json.dumps({"line": "Not tonight!"}))
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, voices=(Placed(redo, 3.2),),
                        thumbnail=False, stream=io.StringIO())  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert detail.startswith("2 line(s)"), detail
    assert "(laid)" not in detail and "3.20-" in detail, detail


@needs_ffmpeg
def test_a_voice_line_with_no_saved_words_is_named(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(
        takes / "take-ep01-t1-raw-v1.mp4", tones=((1.2, 2.0, 440), (3.2, 4.0, 880))
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    bare = make_tone(post_desk / "ep01" / "voices" / "voice-ep01-x-v1.wav", seconds=0.5)
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, voices=(Placed(bare, 0.2),),
                        thumbnail=False, stream=io.StringIO())  # fmt: skip
    detail = next(s.detail for s in result.steps if s.step == "captions")
    assert "!! --voice voice-ep01-x-v1.wav not captioned" in detail, detail
