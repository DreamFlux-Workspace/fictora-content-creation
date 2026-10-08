"""Locked-voice captions start with the voice (L-20261005-6); a master's .ass keeps its captions (L-20261006-8).

(1) New desks: a locked-voice take is timed on a transcript of the take when
one is saved or can be made, on its take-facts windows only where the
transcript did not hear a line, and every caption's start is moved to where
its voice is measured to start: never before it, earlier when the voice runs
ahead of its window (Petty Crimes: ~0.45 s late). Legacy desks keep the windows.

(2) Any desk: with the hook card (or system panels) burned over the captions,
the .ass beside the master holds the dialogue captions AND the overlay, so a
re-burn from it keeps both, and caption readers skip the overlay events.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import make_take, needs_ffmpeg

from creation.captions import CaptionLine, Span, time_lines
from creation.post import caption_timing as ct
from creation.post.master_captions import OVERLAY_NAME, combined_ass
from creation.post.reel import parse_ass_cues
from creation.post.whisper import Word

W = ct.ONSET_WINDOW_SECONDS


def _levels(
    seconds: float, loud: list[tuple[float, float, float]], floor: float
) -> list[float]:
    """0.05 s levels: ``floor`` everywhere, ``level`` inside each ``(start, end, level)``."""

    out = [floor] * int(round(seconds / W))
    for start, end, level in loud:
        for i in range(int(round(start / W)), int(round(end / W))):
            out[i] = level
    return out


def _meter(levels: list[float]):
    def measure(_take: Path, *, window_seconds: float) -> list[float]:
        assert window_seconds == W
        return levels

    return measure


def _line(line_id: str, start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(line_id=line_id, start=start, end=end)


# --- (1) where the voice starts ------------------------------------------------------------------


def test_a_voice_ahead_of_its_window_is_found_where_it_starts() -> None:
    """Petty Crimes (L-20261005-6): the voice runs 0.45 s ahead of the windows, so captions ran late."""

    levels = _levels(6.0, [(1.05, 2.4, -12.0), (3.25, 4.6, -10.0)], floor=-30.0)
    starts = ct.voice_starts(
        Path("t.mp4"),
        [_line("l1", 1.5, 2.8), _line("l2", 3.7, 5.0)],
        measure=_meter(levels),
    )
    assert [s.onset for s in starts] == [1.05, 3.25]
    assert [s.delta for s in starts] == [-0.45, -0.45]
    moved = ct.moved_spans({"l1": Span(1.5, 2.8), "l2": Span(3.7, 5.0)}, starts)
    assert moved == {"l1": Span(1.05, 2.35), "l2": Span(3.25, 4.55)}, (
        "a voice ahead of its window moves the whole caption earlier"
    )


def test_a_voice_inside_its_window_over_the_servers_music_moves_only_the_start() -> (
    None
):
    """Three Payments Late (2026-10-06): music under the voices, each voice 0.05-0.15 s into its window."""

    levels = _levels(7.0, [(0.65, 2.9, -11.0), (3.95, 5.6, -8.0)], floor=-22.0)
    starts = ct.voice_starts(
        Path("t.mp4"),
        [_line("l1", 0.5, 3.0), _line("l2", 3.9, 5.9)],
        measure=_meter(levels),
    )
    assert [s.onset for s in starts] == [0.65, 3.95]
    moved = ct.moved_spans({"l1": Span(0.5, 3.0), "l2": Span(3.9, 5.9)}, starts)
    assert moved == {"l1": Span(0.65, 3.0), "l2": Span(3.95, 5.9)}
    assert "voice vs window +0.05..+0.15s" in ct.summary(starts)


def test_a_short_click_before_the_line_is_not_its_voice() -> None:
    levels = _levels(4.0, [(0.6, 0.65, -5.0), (1.0, 2.0, -10.0)], floor=-40.0)
    [start] = ct.voice_starts(
        Path("t.mp4"), [_line("l1", 1.0, 2.0)], measure=_meter(levels)
    )
    assert start.onset == 1.0, "one 0.05 s spike is a click, not the start of a voice"


def test_a_line_with_no_voice_near_it_keeps_its_window() -> None:
    levels = _levels(4.0, [], floor=-60.0)
    [start] = ct.voice_starts(
        Path("t.mp4"), [_line("l1", 1.0, 2.0)], measure=_meter(levels)
    )
    assert start.onset is None
    assert ct.moved_spans({"l1": Span(1.0, 2.0)}, [start]) == {"l1": Span(1.0, 2.0)}
    assert ct.voice_floors([start]) == {}


def test_the_search_never_reaches_back_into_the_previous_line() -> None:
    levels = _levels(5.0, [(0.5, 2.0, -10.0), (2.3, 3.5, -10.0)], floor=-40.0)
    starts = ct.voice_starts(
        Path("t.mp4"),
        [_line("l1", 0.5, 2.0), _line("l2", 2.3, 3.5)],
        measure=_meter(levels),
    )
    assert starts[1].onset == 2.3, (
        "the first line's voice (until 2.0 s) is not the second's start"
    )


# --- (1) timing on the transcript first, never before the voice ----------------------------------

LINES = [CaptionLine("l1", "Wait for me here."), CaptionLine("l2", "Not tonight.")]
HEARD = [Word(1.02, 1.3, "Wait"), Word(1.3, 1.5, "for"), Word(1.5, 1.7, "me"),
         Word(1.7, 1.95, "here.")]  # fmt: skip


def test_a_line_the_transcript_heard_is_timed_on_its_words_and_one_it_missed_on_its_window() -> (
    None
):
    known = [Span(1.45, 2.45), Span(3.65, 4.45)]
    timing = time_lines(
        LINES, duration=5.0, words=HEARD, known=known, known_after_words=True
    )
    assert timing.methods == ("words", "lines")
    assert timing.anchors[0].start == pytest.approx(1.02)
    assert timing.anchors[1] == Span(3.65, 4.45)
    # The default (and every legacy desk) keeps the windows first.
    before = time_lines(LINES, duration=5.0, words=HEARD, known=known)
    assert before.methods == ("lines", "lines") and before.anchors[0] == Span(
        1.45, 2.45
    )


def test_no_caption_starts_before_its_measured_voice() -> None:
    stretched = [
        Word(0.4, 1.3, "Wait"),
        *HEARD[1:],
    ]  # Whisper stretched the first word back
    timing = time_lines(
        LINES, duration=5.0, words=stretched, known=[Span(1.0, 2.0), Span(3.2, 4.0)],
        known_after_words=True, floors=[1.0, 3.25], per_word=True,
    )  # fmt: skip
    assert timing.anchors[0].start == 1.0
    assert timing.anchors[1].start == 3.25
    assert timing.word_cues[0] and timing.word_cues[0][0].start >= 1.0
    # A hand start is the operator's and is never moved.
    hand = time_lines(LINES, duration=5.0, line_starts=[0.5, 3.0], floors=[1.0, 3.25])
    assert [a.start for a in hand.anchors] == [0.5, 3.0]


# --- (2) the master's .ass keeps the captions beside the overlay ---------------------------------

CAPTIONS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: House,Arial,64
Style: Italic,Georgia,60

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:01.50,House,,0,0,0,,{\\an8\\pos(540,1190)}Wait for
Dialogue: 0,0:00:01.50,0:00:02.00,House,,0,0,0,,{\\an8\\pos(540,1190)}Wait for me
"""

HOOK = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Hook,Arial,72

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.50,Hook,,0,0,0,,{\\an8\\pos(540,300)}She lied first
"""


def test_the_combined_file_holds_both_and_readers_see_only_the_captions() -> None:
    text = combined_ass(CAPTIONS, HOOK)
    assert "Style: House,Arial,64" in text and "Style: Hook,Arial,72" in text
    assert "Wait for me" in text and "She lied first" in text, (
        "a re-burn of this file draws both"
    )
    hook_line = next(line for line in text.splitlines() if "She lied first" in line)
    assert f",Hook,{OVERLAY_NAME}," in hook_line and hook_line.startswith(
        "Dialogue: 10,"
    ), "the hook card is named an overlay and drawn above the captions, as burned"
    assert [c.text for c in parse_ass_cues(text)] == ["Wait for", "Wait for me"]
    assert len(parse_ass_cues(text, overlays=True)) == 3
    # A second overlay (panels, then the hook) adds to the same file.
    again = combined_ass(text, HOOK.replace("She lied first", "Second card"))
    assert [c.text for c in parse_ass_cues(again)] == ["Wait for", "Wait for me"]
    assert "She lied first" in again and "Second card" in again


def test_a_take_with_no_captions_keeps_just_the_overlay_and_a_canvas_mismatch_is_refused() -> (
    None
):
    alone = combined_ass(None, HOOK)
    assert "She lied first" in alone and parse_ass_cues(alone) == []
    with pytest.raises(ValueError, match="drawn on"):
        combined_ass(CAPTIONS.replace("PlayResY: 1920", "PlayResY: 1344"), HOOK)


# --- finish, end to end ----------------------------------------------------------------------------

#: Kenji's voice 1.0-2.0 s, Aya's 3.2-4.0 s; the facts place both 0.45 s later (Petty Crimes).
LATE = [
    {"line_id": "l1", "cast_id": "cast_kenji", "start_s": 1.45, "end_s": 2.45, "off_screen": False},
    {"line_id": "l2", "cast_id": "cast_aya", "start_s": 3.65, "end_s": 4.45, "off_screen": False},
]  # fmt: skip


def _locked_desk(post_desk: Path) -> Path:
    from test_post_finish import FACTS, TWO_LINES

    facts = copy.deepcopy(FACTS)
    facts["take_facts"]["soundtrack"] = {
        "mode": "target_audio", "reason": None, "track_url": "https://x/track.wav",
        "lines": LATE, "native_foley": False,
    }  # fmt: skip
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    return post_desk


def _finish(post_desk: Path, transcriber):
    from test_post_finish import fake_bed, fake_sfx

    from creation.post.finish import run_finish

    out = io.StringIO()
    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        transcriber=transcriber, cut_meter=lambda _take: (2.5,), stream=out,
    )  # fmt: skip
    return result, out.getvalue()


def _cap_cues(post_desk: Path) -> list:
    ass = sorted((post_desk / "ep01" / "takes").glob("take-ep01-t1-cap-v*.ass"))[-1]
    return parse_ass_cues(ass.read_text(encoding="utf-8"))


@needs_ffmpeg
def test_finish_times_a_locked_voice_take_on_its_transcript_once(
    post_desk: Path,
) -> None:
    _locked_desk(post_desk)
    asked: list[str] = []

    def transcriber(desk: Path, episode: int, take_id: str) -> Path:
        asked.append(take_id)
        path = desk / "ep01" / "takes" / "take-ep01-t1-raw-v1-review-words-v1.json"
        # Heard a little after each tone starts: the words, not the measured onset, place the captions.
        words = [("Wait", 1.1, 1.3), ("for", 1.3, 1.5), ("me", 1.5, 1.7), ("here.", 1.7, 1.95),
                 ("Not", 3.3, 3.5), ("tonight.", 3.5, 3.95)]  # fmt: skip
        path.write_text(
            json.dumps(
                {"words": [{"word": w, "start": s, "end": e} for w, s, e in words]}
            )
        )
        return path

    result, log = _finish(post_desk, transcriber)
    assert result.complete, log
    assert asked == ["t1"], "made once on the server for this take"
    detail = next(s for s in result.steps if s.step == "captions").detail
    assert "transcript made on the server" in detail and "(words)" in detail
    starts = sorted({round(c.start, 2) for c in _cap_cues(post_desk)})
    assert starts[0] == pytest.approx(1.1, abs=0.02), (
        "on its first word, not the late window (1.45 s)"
    )
    assert any(abs(s - 3.3) <= 0.02 for s in starts)


@needs_ffmpeg
def test_finish_without_a_transcript_moves_each_window_onto_its_voice(
    post_desk: Path,
) -> None:
    _locked_desk(post_desk)

    def no_url(*_: object) -> Path:
        raise ValueError("no stored URL for ep01 t1")

    result, log = _finish(post_desk, no_url)
    assert result.complete, log
    detail = next(s for s in result.steps if s.step == "captions").detail
    assert "each moved to where its voice starts" in detail
    assert "captions start with the voice" in detail
    firsts = [c.start for c in _cap_cues(post_desk)]
    assert min(firsts) == pytest.approx(1.0, abs=0.06), "0.45 s earlier than the window"
    assert any(abs(s - 3.2) <= 0.06 for s in firsts)


@needs_ffmpeg
def test_a_legacy_desk_keeps_its_planned_windows_and_is_never_transcribed(
    post_desk: Path,
) -> None:
    from creation.rules_epoch import run_rules_epoch

    _locked_desk(post_desk)
    run_rules_epoch(post_desk, set_to="legacy", out=io.StringIO())

    def never(*_: object) -> Path:
        raise AssertionError(
            "a legacy desk's locked-voice take is captioned from its windows"
        )

    result, log = _finish(post_desk, never)
    assert result.complete, log
    detail = next(s for s in result.steps if s.step == "captions").detail
    assert "the locked-voice dialogue track; no transcript" in detail
    assert min(c.start for c in _cap_cues(post_desk)) == pytest.approx(1.45, abs=0.01)


@needs_ffmpeg
def test_with_the_hook_card_on_the_masters_ass_keeps_the_dialogue_captions(
    post_desk: Path,
) -> None:
    """L-20261006-8: a re-burn from the master's .ass dropped every dialogue caption."""

    from test_hook_overlay import ON, _set_hook
    from test_post_finish import FACTS, TWO_LINES, _board, fake_bed, fake_sfx

    from creation.post.finish import run_finish
    from creation.post.finish_record import _load

    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _board(post_desk)
    _set_hook(post_desk, ON)
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip
    assert next(s for s in result.steps if s.step == "hook-line").status == "ran"
    record = sorted((post_desk / "ep01" / "takes").glob("take-ep01-t1-finish-v*.json"))[
        -1
    ]
    master = _load(record).resolve(post_desk, "master")
    assert master is not None and "-hook-" in master.name
    text = master.with_suffix(".ass").read_text(encoding="utf-8")
    captions = parse_ass_cues(text)
    assert captions and [c.text for c in captions] == [
        c.text for c in _cap_cues(post_desk)
    ], "the master's .ass holds the dialogue captions"
    assert len(parse_ass_cues(text, overlays=True)) > len(captions), (
        "... and the hook card"
    )


def test_a_transcript_heard_well_after_the_measured_voice_is_not_trusted() -> None:
    """A transcript a second late (the windows' own test, L-20261005-6 keeps it): the window stays."""

    late = [Word(w.start + 1.0, w.end + 1.0, w.text) for w in HEARD]
    timing = time_lines(
        LINES, duration=5.0, words=late, known=[Span(1.0, 2.0), Span(3.2, 4.0)],
        known_after_words=True, floors=[1.0, 3.2],
    )  # fmt: skip
    assert timing.methods == ("lines", "lines")
    assert timing.anchors[0] == Span(1.0, 2.0)
