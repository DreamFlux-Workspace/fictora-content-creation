"""The model-voice line check: finish STOPS on a missing, wrong, repeated, extra or wrong-shot line.

Founder decision (8 Oct 2026, learning L-20260924-10): a show filmed in the video
model's own voices keeps them, but the take's words are compared with the script
before finish delivers; a fault stops finish with the re-film command
(re-filming, not patching: a laid voice line breaks lip sync). Locked-voice takes
are unchanged (their words are checked before filming).
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from conftest import SPINE, make_take, needs_ffmpeg

from creation.post import line_check as lc
from creation.post.finish import run_finish
from creation.post.line_check import ScriptLine, check_take_lines, normalise
from creation.post.whisper import Word, _tokens
from test_post_finish import FACTS, fake_bed, fake_sfx

KENJI = "I told you to wait for me here."
AYA = "You never listen to anything I say."
LINES = [
    ScriptLine(1, "l1", KENJI, speaker="Kenji", shot_index=1, window=(0.0, 2.5)),
    ScriptLine(2, "l2", AYA, speaker="Aya", shot_index=2, window=(2.5, 5.0)),
]


def words(*spans: tuple[str, float, float]) -> list[Word]:
    """Whisper-like words, spread evenly over each span."""

    out: list[Word] = []
    for text, start, end in spans:
        parts = text.split()
        step = (end - start) / len(parts)
        out += [
            Word(start + i * step, start + (i + 1) * step, p)
            for i, p in enumerate(parts)
        ]
    return out


def kinds(check: lc.LineCheck) -> list[str]:
    return [fault.kind for fault in check.faults]


# --- the matcher ---------------------------------------------------------------------------------------


def test_a_clean_take_passes() -> None:
    check = check_take_lines(
        LINES, words((KENJI, 0.2, 2.0), (AYA, 2.8, 4.5)), take_id="t1"
    )
    assert check.ok and check.faults == [] and len(check.heard) == 2


@pytest.mark.parametrize(
    ("kenji", "aya"),
    [
        ("I told you to wait right here.", AYA),  # one word changed, one dropped
        (
            "I told you to wait for me here!",
            "You never listen to anything I say!",
        ),  # punctuation
        (
            "I told you, wait for me here.",
            "you NEVER listen to anything i say",
        ),  # case, a dropped "to"
        (
            "I told you to wait for me over here.",
            "You never ever listen to anything I say.",
        ),  # a word added
    ],
)
def test_small_wording_differences_pass(kenji: str, aya: str) -> None:
    check = check_take_lines(
        LINES, words((kenji, 0.2, 2.0), (aya, 2.8, 4.5)), take_id="t1"
    )
    assert check.ok, [f.describe() for f in check.faults]


def test_a_missing_line_is_named() -> None:
    check = check_take_lines(LINES, words((KENJI, 0.2, 2.0)), take_id="t1")
    assert kinds(check) == ["missing"]
    assert (
        check.faults[0]
        .describe()
        .startswith('MISSING LINE: line 2 (Aya) "You never listen')
    )


def test_substantially_wrong_words_say_what_was_heard() -> None:
    heard = "The cat ate my homework yesterday morning."
    check = check_take_lines(
        LINES, words((KENJI, 0.2, 2.0), (heard, 2.8, 4.5)), take_id="t1"
    )
    assert kinds(check) == ["wrong"]
    text = check.faults[0].describe()
    assert (
        text.startswith("WRONG WORDS: line 2 (Aya)")
        and heard in text
        and "2.80-4.50s" in text
    )


def test_half_a_line_said_is_wrong_words() -> None:
    check = check_take_lines(
        LINES,
        words((KENJI, 0.2, 2.0), ("You never listen to me.", 2.8, 4.5)),
        take_id="t1",
    )
    assert kinds(check) == ["wrong"]


def test_a_line_matched_under_the_threshold_is_wrong_words() -> None:
    line = [ScriptLine(1, "l1", "I told you to wait for me right here tonight.")]
    # 6 of its 10 words heard in order: found, but under 70%.
    check = check_take_lines(
        line,
        words(("I told you to wait for him there now later.", 0.0, 3.0)),
        take_id="t1",
    )
    assert (
        kinds(check) == ["wrong"]
        and "60% of its words heard, 70% needed" in check.faults[0].describe()
    )


def test_a_line_with_many_invented_words_inside_is_wrong_words() -> None:
    heard = "You never really ever listen to, like, anything that I say."
    check = check_take_lines(
        LINES, words((KENJI, 0.2, 2.0), (heard, 2.8, 4.8)), take_id="t1"
    )
    assert (
        kinds(check) == ["wrong"]
        and "words the script does not have" in check.faults[0].describe()
    )


def test_a_repeated_line_is_named() -> None:
    check = check_take_lines(
        LINES,
        words((KENJI, 0.2, 1.2), (KENJI, 1.3, 2.3), (AYA, 2.8, 4.5)),
        take_id="t1",
    )
    assert kinds(check) == ["repeated"]
    assert (
        check.faults[0]
        .describe()
        .startswith('REPEATED LINE: line 1 (Kenji) "I told you')
    )


def test_invented_extra_speech_is_named() -> None:
    check = check_take_lines(
        LINES,
        words(
            (KENJI, 0.2, 2.0),
            ("and then we go home tonight", 2.05, 2.7),
            (AYA, 2.8, 4.5),
        ),
        take_id="t1",
    )
    assert kinds(check) == ["extra"]
    assert '"and then we go home tonight"' in check.faults[0].describe()


def test_fillers_and_transcript_noise_are_not_extra_speech() -> None:
    check = check_take_lines(
        LINES,
        words(
            ("um", 0.0, 0.1),
            (KENJI, 0.2, 2.0),
            ("Thank you.", 2.1, 2.4),
            ("uh oh hmm", 2.45, 2.7),
            (AYA, 2.8, 4.5),
        ),
        take_id="t1",
    )
    assert check.ok, [f.describe() for f in check.faults]


def test_a_line_on_the_wrong_shot_is_named() -> None:
    # Kenji's line, planned for shot 1 (0-2.5 s), is said over shot 2.
    check = check_take_lines(
        LINES, words((AYA, 0.2, 2.0), (KENJI, 2.8, 4.8)), take_id="t1"
    )
    assert "wrong_shot" in kinds(check)
    shot = next(f for f in check.faults if f.kind == "wrong_shot").describe()
    assert shot.startswith("WRONG SHOT:") and "shot 1 (0.00-2.50s)" in shot


def test_a_stretched_first_word_is_not_a_wrong_shot() -> None:
    # Whisper stretches a line's first word back over the silence before it (Hanakaze ep 6).
    late = [ScriptLine(1, "l1", AYA, window=(12.1, 15.0), shot_index=4)]
    heard = [
        Word(7.0, 12.6, "You"),
        *words(("never listen to anything I say.", 12.6, 14.0)),
    ]
    assert check_take_lines(late, heard, take_id="t1").ok


def test_an_off_screen_line_has_no_wrong_shot_check() -> None:
    off = [ScriptLine(1, "l1", KENJI, off_screen=True, window=(0.0, 2.5))]
    assert check_take_lines(off, words((KENJI, 3.0, 4.8)), take_id="t1").ok


def test_a_short_line_the_transcript_missed_is_checked_by_ear_not_a_stop() -> None:
    short = [*LINES, ScriptLine(3, "l3", "Hey!")]
    check = check_take_lines(
        short, words((KENJI, 0.2, 2.0), (AYA, 2.8, 4.5)), take_id="t1"
    )
    assert check.ok and any(
        note.startswith("CHECK BY EAR: line 3") for note in check.notes
    )


def test_muted_speech_is_not_extra_speech() -> None:
    heard = words(
        (KENJI, 0.2, 2.0), ("and then we go home tonight", 2.05, 2.7), (AYA, 2.8, 4.5)
    )
    assert check_take_lines(LINES, heard, take_id="t1", muted=[(2.0, 2.75)]).ok


def test_a_hand_line_patches_only_an_off_screen_line() -> None:
    off = [LINES[0], ScriptLine(2, "l2", AYA, off_screen=True)]
    check = check_take_lines(
        off, words((KENJI, 0.2, 2.0)), take_id="t1", patched=["l2"]
    )
    assert check.ok and "heard off screen" in check.notes[0]
    on = check_take_lines(LINES, words((KENJI, 0.2, 2.0)), take_id="t1", patched=["l2"])
    assert kinds(on) == ["missing"] and "breaks lip sync" in on.notes[0]


def test_numbers_and_contractions_are_normalised() -> None:
    assert _tokens(normalise("Don't be late: 3 minutes, 1,200 dollars")) == _tokens(
        "dont be late three minutes one thousand two hundred dollars"
    )
    line = [ScriptLine(1, "l1", "We leave at 3, don't be late.")]
    assert check_take_lines(
        line, words(("We leave at three. Do not be late.", 0.0, 2.0)), take_id="t1"
    ).ok


# --- Japanese and Korean ----------------------------------------------------------------------------------


def test_japanese_numbers_and_full_width_digits_normalise_to_kanji() -> None:
    assert normalise("３人で", "ja") == normalise("三人で", "ja") == "三人で"
    assert normalise("12時", "ja") == "十二時"


def test_korean_numbers_normalise_to_sino_korean() -> None:
    assert normalise("3층", "ko") == "삼층"
    assert normalise("15분", "ko") == "십오분"


def test_a_japanese_line_matches_character_by_character_and_misses_are_found() -> None:
    lines = [
        ScriptLine(1, "l1", "いらっしゃいませー！"),
        ScriptLine(2, "l2", "…笑ってますけど？"),
    ]
    heard = [Word(0.9, 2.2, "いらっしゃいませ!")]
    check = check_take_lines(lines, heard, take_id="t1", language="ja")
    assert kinds(check) == ["missing"] and check.faults[0].line.line_id == "l2"
    clean = [*heard, Word(13.1, 14.8, "笑ってますけど?")]
    assert check_take_lines(lines, clean, take_id="t1", language="ja").ok


def test_a_korean_line_ignores_spacing_and_flags_wrong_words() -> None:
    lines = [ScriptLine(1, "l1", "보리야, 어디 갔었어?")]
    assert check_take_lines(
        lines, [Word(0.0, 1.5, "보리야 어디갔었어")], take_id="t1", language="ko"
    ).ok
    wrong = check_take_lines(
        lines, [Word(0.0, 1.5, "오늘 날씨가 정말 좋네요")], take_id="t1", language="ko"
    )
    assert kinds(wrong) == ["wrong"]


def test_a_transcript_in_another_language_is_not_read() -> None:
    lines = [ScriptLine(1, "l1", "来たよ！寂しかった？")]
    check = check_take_lines(
        lines,
        words(("Here I am! Were you lonely?", 0.0, 2.0)),
        take_id="t1",
        language="ja",
    )
    assert not check.ok and check.unread and check.faults == []


# --- finish -----------------------------------------------------------------------------------------------

#: Kenji's line in shot 1, Aya's in shot 2 (the take facts' line windows).
LINE_FACTS = copy.deepcopy(FACTS)
LINE_FACTS["take_facts"]["lines"] = [
    {
        "line_id": "l1",
        "count": 1,
        "shot_index": 1,
        "start_seconds": 0.0,
        "end_seconds": 2.5,
    },
    {
        "line_id": "l2",
        "count": 1,
        "shot_index": 2,
        "start_seconds": 2.5,
        "end_seconds": 5.0,
    },
]
MODEL_SPINE = copy.deepcopy(SPINE)
MODEL_SPINE["beats"][0]["dialogue_lines"] = [
    {"line_id": "l1", "cast_id": "cast_kenji", "text": KENJI},
    {"line_id": "l2", "cast_id": "cast_aya", "text": AYA},
]


def _model_take(
    desk: Path,
    heard: list[tuple[str, float, float]],
    facts: dict = LINE_FACTS,
    *,
    tones: tuple[tuple[float, float, int], ...] | None = None,
) -> Path:
    """A model-voice take whose audio holds a "voice" (a tone) wherever ``heard`` has words."""

    (desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(MODEL_SPINE))
    raw = make_take(
        desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
        tones=tones
        if tones is not None
        else tuple(
            (start, end, 440 + 220 * i) for i, (_t, start, end) in enumerate(heard)
        ),
    )
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts))
    body = {
        "words": [
            {"word": w.text, "start": w.start, "end": w.end} for w in words(*heard)
        ]
    }
    (desk / "ep01" / "takes" / "take-ep01-t1-raw-v1-review-words-v1.json").write_text(
        json.dumps(body)
    )
    return raw


def _finish(desk: Path, **kw: object) -> tuple[object, str]:
    out = io.StringIO()
    result = run_finish(
        desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        transcriber=_no_transcriber, stream=out, **kw,
    )  # fmt: skip
    return result, out.getvalue()


def _no_transcriber(*_a: object) -> Path:
    raise AssertionError(
        "a saved transcript of this take is on the desk: nothing is asked of the server"
    )


def _notes(desk: Path) -> str:
    return (desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")


FAULTS = {
    "missing": ([(KENJI, 0.2, 2.0)], 'MISSING LINE: line 2 (Aya) "You never listen'),
    "wrong": (
        [(KENJI, 0.2, 2.0), ("The cat ate my homework yesterday morning.", 2.8, 4.5)],
        "WRONG WORDS: line 2",
    ),
    "repeated": (
        [(KENJI, 0.2, 1.2), (KENJI, 1.3, 2.3), (AYA, 2.8, 4.5)],
        "REPEATED LINE: line 1 (Kenji)",
    ),
    "extra": (
        [
            (KENJI, 0.2, 2.0),
            ("and then we go home tonight", 2.05, 2.7),
            (AYA, 2.8, 4.5),
        ],
        "EXTRA SPEECH",
    ),
    "wrong_shot": ([(AYA, 0.2, 2.0), (KENJI, 2.8, 4.8)], "WRONG SHOT: line 1 (Kenji)"),
}


@needs_ffmpeg
@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_each_fault_stops_finish_before_anything_is_made(
    post_desk: Path, fault: str
) -> None:
    heard, expected = FAULTS[fault]
    _model_take(post_desk, heard)
    result, printed = _finish(post_desk)
    assert result.line_faults and not result.complete and result.steps == []
    assert expected in result.stopped
    assert "ep01 t1: the take does not say its script" in result.stopped
    assert (
        f"fictora-produce film --desk {post_desk} --episode 1 --take t1 --cause"
        in result.stopped
    )
    assert "--confirm-spend" in result.stopped and "$0.40" in result.stopped
    assert (
        "breaks lip sync" in result.stopped
        and "--accept-line-mismatch t1" in result.stopped
    )
    assert "!! STOPPED:" in printed
    assert "STOPPED on the line check" in _notes(post_desk)
    assert not list((post_desk / "ep01" / "takes").glob("*-finish-v*.json"))


@needs_ffmpeg
def test_a_clean_model_voice_take_finishes(post_desk: Path) -> None:
    _model_take(post_desk, [(KENJI, 0.2, 2.0), (AYA, 2.8, 4.5)])
    result, printed = _finish(post_desk)
    assert not result.line_faults and not result.stopped and result.steps
    assert "line check: 2 of 2 scripted line(s) heard as written" in printed


@needs_ffmpeg
def test_the_override_delivers_and_logs_the_faults(post_desk: Path) -> None:
    _model_take(post_desk, [(KENJI, 0.2, 2.0)])
    result, printed = _finish(post_desk, accept_line_mismatch=("t1",))
    assert not result.line_faults and not result.stopped and result.steps
    assert "delivered anyway (--accept-line-mismatch t1)" in printed
    notes = _notes(post_desk)
    assert (
        "ACCEPTED WITH FAULTS (--accept-line-mismatch t1" in notes
        and "MISSING LINE: line 2" in notes
    )


@needs_ffmpeg
def test_an_override_for_another_take_does_not_deliver_this_one(
    post_desk: Path,
) -> None:
    _model_take(post_desk, [(KENJI, 0.2, 2.0)])
    result, _ = _finish(post_desk, accept_line_mismatch=("t2",))
    assert result.line_faults


@needs_ffmpeg
def test_a_locked_voice_take_is_not_line_checked(post_desk: Path) -> None:
    locked = copy.deepcopy(LINE_FACTS)
    locked["take_facts"]["soundtrack"] = {
        "mode": "target_audio",
        "reason": None,
        "lines": [
            {
                "line_id": "l1",
                "cast_id": "cast_kenji",
                "start_s": 0.2,
                "end_s": 2.0,
                "off_screen": False,
            },
            {
                "line_id": "l2",
                "cast_id": "cast_aya",
                "start_s": 2.8,
                "end_s": 4.5,
                "off_screen": False,
            },
        ],
    }
    # The transcript (of a take that dropped a line) is never read on a locked-voice take.
    _model_take(post_desk, [(KENJI, 0.2, 2.0)], facts=locked)
    result, printed = _finish(post_desk)
    assert not result.line_faults and "[lines]" not in printed and result.steps


@needs_ffmpeg
def test_no_transcript_makes_one_on_the_server_and_checks_it(post_desk: Path) -> None:
    _model_take(post_desk, [(KENJI, 0.2, 2.0)])
    saved = post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1-review-words-v1.json"
    moved = saved.with_name("made-by-server.json")
    saved.rename(moved)
    asked: list[str] = []

    def transcriber(desk: Path, episode: int, take_id: str) -> Path:
        asked.append(take_id)
        return moved

    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
                        transcriber=transcriber, stream=out)  # fmt: skip
    assert asked == ["t1"] and result.line_faults
    assert "made on the server now, $0" in out.getvalue()


@needs_ffmpeg
def test_a_check_that_cannot_run_is_loud_but_not_a_stop(post_desk: Path) -> None:
    _model_take(post_desk, [(KENJI, 0.2, 2.0)])
    (post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1-review-words-v1.json").unlink()

    def transcriber(*_a: object) -> Path:
        raise ValueError("no stored URL")

    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
                        transcriber=transcriber, stream=out)  # fmt: skip
    assert not result.line_faults and result.steps
    assert (
        "!! line check NOT RUN" in out.getvalue()
        and "Listen to every line" in out.getvalue()
    )


def test_the_cli_passes_the_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation import cli_post

    seen: dict[str, object] = {}

    class Done(Exception):
        pass

    def fake_finish(*_a: object, **kw: object) -> None:
        seen.update(kw)
        raise Done

    monkeypatch.setattr(cli_post, "run_finish", fake_finish)
    from creation.cli_produce import main

    with pytest.raises(Done):
        main(
            [
                "finish",
                "--desk",
                str(tmp_path),
                "--take",
                "t2",
                "--accept-line-mismatch",
                "t2",
            ]
        )
    assert seen["accept_line_mismatch"] == ("t2",)


# --- a clip to hear the line, and a quiet line the transcript missed (Noodle24 L-20261008-38) --------------


def _levels(voice: tuple[float, float] | None, *, seconds: float = 5.0) -> list[float]:
    """A take's levels: a quiet room (-60 dBFS), Kenji at 0.2-2.0 s, and ``voice`` (a quiet line) if given."""

    out = []
    for frame in range(int(seconds / lc.LEVEL_STEP_SECONDS)):
        t = frame * lc.LEVEL_STEP_SECONDS
        loud = 0.2 <= t < 2.0 or (voice is not None and voice[0] <= t < voice[1])
        out.append(-30.0 if loud else -60.0)
    return out


def test_a_missing_line_is_heard_in_its_own_shot_not_over_the_line_before() -> None:
    check = check_take_lines(LINES, words((KENJI, 0.2, 2.4)), take_id="t1")
    (fault,) = check.faults
    # Aya's shot is 2.5-5.0 s (2.1-5.4 s padded); Kenji runs to 2.4 s, so the clip starts after him.
    assert fault.kind == "missing" and fault.listen == (2.4, 5.4)


def test_a_fault_heard_somewhere_is_played_a_little_either_side() -> None:
    check = check_take_lines(
        LINES,
        words(
            (KENJI, 0.2, 2.0), ("The cat ate my homework yesterday morning.", 2.8, 4.5)
        ),
        take_id="t1",
    )
    (fault,) = check.faults
    assert fault.kind == "wrong" and fault.listen == (2.4, 4.9)


def test_a_quiet_line_the_transcript_missed_is_check_by_ear_not_a_stop() -> None:
    check = check_take_lines(LINES, words((KENJI, 0.2, 2.0)), take_id="t1")
    assert lc.grade_unheard_lines(check, _levels((3.0, 3.6))) == 1
    assert check.ok and check.faults == []
    (by_ear,) = check.by_ear
    assert (
        by_ear.kind == "unclear"
        and by_ear.line is not None
        and by_ear.line.line_id == "l2"
    )
    assert by_ear.describe().startswith('CHECK BY EAR: line 2 (Aya) "You never listen')


def test_a_line_that_was_really_dropped_stays_missing() -> None:
    check = check_take_lines(LINES, words((KENJI, 0.2, 2.0)), take_id="t1")
    assert lc.grade_unheard_lines(check, _levels(None)) == 0
    assert kinds(check) == ["missing"]


def test_a_click_is_not_a_line() -> None:
    check = check_take_lines(LINES, words((KENJI, 0.2, 2.0)), take_id="t1")
    levels = _levels(None)
    levels[70] = -20.0  # 50 ms at 3.5 s: a door, not a voice
    assert lc.grade_unheard_lines(check, levels) == 0
    assert kinds(check) == ["missing"]


@needs_ffmpeg
def test_a_stop_cuts_a_clip_of_each_fault_to_hear_before_choosing(
    post_desk: Path,
) -> None:
    _model_take(post_desk, [(KENJI, 0.2, 2.0)])
    result, _printed = _finish(post_desk)
    assert result.line_faults
    clip = post_desk / "ep01" / "takes" / "listen" / "ep01-t1-line2-missing.wav"
    assert clip.exists() and clip.stat().st_size > 1000
    assert "Listen before choosing" in result.stopped and str(clip) in result.stopped


@needs_ffmpeg
def test_a_quiet_line_the_transcript_missed_does_not_stop_finish(
    post_desk: Path,
) -> None:
    # Aya is audible at 2.8-4.5 s, but the transcript did not write her line down.
    _model_take(
        post_desk, [(KENJI, 0.2, 2.0)], tones=((0.2, 2.0, 440), (2.8, 4.5, 880))
    )
    result, printed = _finish(post_desk)
    assert not result.line_faults and not result.stopped and result.steps
    assert 'CHECK BY EAR: line 2 (Aya) "You never listen' in printed
    clip = post_desk / "ep01" / "takes" / "listen" / "ep01-t1-line2-unclear.wav"
    assert clip.exists() and f"listen: line 2 -> {clip}" in printed
    assert "CHECK BY EAR: line 2" in _notes(post_desk)
