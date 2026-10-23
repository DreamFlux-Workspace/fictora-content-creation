"""Captions follow the speech timestamps: each word shows when it is said, as it was said (6 Oct 2026).

SCP-173 Blink ep 1 was captioned 0.6-1.4 s late ("door!" captioned about a
second after it was said): the finish spread each line's words across the
line's span. It now times every word on the transcript (``take-epNN-tK-words-*-vN.json``), checked against the
take's speech so a word Whisper stamped over a pause starts when its sound
does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from creation.captions import (
    CaptionLine,
    Span,
    build_line_cues,
    heard_word_cues,
    parse_silencedetect,
    speech_spans,
    time_lines,
    word_onset,
)
from creation.post.whisper import Word, load_words

SPINE = {
    "title": "Blink",
    "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
    "beats": [
        {"beat_id": "b1", "episode_id": "episode_01", "ordinal": 1,
         "dialogue_lines": [{"line_id": "l1", "text": "D-9341. Do not break eye contact."}]},
        {"beat_id": "b2", "episode_id": "episode_01", "ordinal": 2,
         "dialogue_lines": [{"line_id": "l2", "text": "Please— open the door!"}]},
        {"beat_id": "b3", "episode_id": "episode_01", "ordinal": 3,
         "dialogue_lines": [{"line_id": "l3", "text": "No no no— I'm not blinking, I'm NOT—"}]},
    ],
}  # fmt: skip

#: The desk's ``take-ep01-t1-words-en-v6.json`` (Whisper on the raw take), word for word.
SCP_CHUNKS = [
    ([0.03, 0.21], " D"), ([0.21, 1.45], "-9341."), ([1.45, 1.93], " Do"), ([1.93, 2.17], " not"),
    ([2.17, 2.43], " break"), ([2.43, 2.71], " eye"), ([2.71, 3.43], " contact."),
    ([6.29, 8.19], " Please,"), ([8.19, 8.89], " open"), ([8.89, 9.15], " the"), ([9.15, 10.63], " door!"),
    ([11.13, 12.01], " No,"), ([12.01, 12.15], " no,"), ([12.15, 12.43], " no!"), ([12.43, 13.07], " I'm"),
    ([13.07, 13.25], " not"), ([13.25, 13.83], " blinking!"), ([13.83, 14.01], " I'm"), ([14.01, 14.35], " not!"),
]  # fmt: skip

#: ``silencedetect`` (-30 dB, 0.3 s) on the sound that transcript heard (``…-words-en-v6.wav``).
SCP_SILENCES = """
silence_start: 1.462375
silence_end: 1.944438 | silence_duration: 0.482063
silence_start: 3.453937
silence_end: 4.198 | silence_duration: 0.744062
silence_start: 4.728937
silence_end: 6.604875 | silence_duration: 1.875938
silence_start: 6.671125
silence_end: 7.829125 | silence_duration: 1.158
silence_start: 8.242875
silence_end: 8.827938 | silence_duration: 0.585063
silence_start: 9.755125
silence_end: 10.706125 | silence_duration: 0.951
silence_start: 10.7165
silence_end: 11.142625 | silence_duration: 0.426125
silence_start: 11.269063
silence_end: 11.879688 | silence_duration: 0.610625
silence_start: 12.508062
silence_end: 13.004437 | silence_duration: 0.496375
silence_start: 14.529563
silence_end: 15.104 | silence_duration: 0.574438
"""


def _words_json(tmp_path: Path, chunks: list = SCP_CHUNKS) -> Path:  # type: ignore[type-arg]
    path = tmp_path / "take-ep01-t1-words-en-v6.json"
    path.write_text(
        json.dumps({"chunks": [{"timestamp": t, "text": x} for t, x in chunks]})
    )
    return path


def _speech() -> list[Span]:
    return speech_spans(parse_silencedetect(SCP_SILENCES, 15.104), 15.104)


def _starts(cues: list, word: str) -> list[float]:  # type: ignore[type-arg]
    """When each cue whose newest word is ``word`` starts (a flicker cue ends with the word just said)."""

    return [
        c.start for c in cues if c.text.split()[-1].strip(",.!…—-").casefold() == word
    ]


# --- each word when it is said ---------------------------------------------------------------------


def test_a_word_whisper_stamped_over_a_pause_starts_when_its_sound_does() -> None:
    """SCP ep 1: ``Please,`` heard 6.29-8.19 s and ``open`` 8.19 s; the sound starts 7.83 s and 8.83 s."""

    words = load_words_from(SCP_CHUNKS)
    speech = _speech()
    by_text = {w.text: w for w in words}
    assert word_onset(by_text["Please,"], speech) == pytest.approx(7.83, abs=0.02)
    assert word_onset(by_text["open"], speech) == pytest.approx(8.83, abs=0.02)
    # A word that starts in its sound keeps the transcript's time (door! is stretched over the silence after it).
    assert word_onset(by_text["the"], speech) == pytest.approx(8.89)
    assert word_onset(by_text["door!"], speech) == pytest.approx(9.15)
    # Never before the sound: no word of the line starts before its speech.
    cues, _ = heard_word_cues("Please— open the door!", words[7:11], speech=speech)
    assert [c.text for c in cues] == ["Please—", "open", "the", "door!"]
    assert cues[0].start >= 7.83 - 0.08 and cues[1].start >= 8.83 - 0.08


def load_words_from(chunks: list) -> tuple[Word, ...]:  # type: ignore[type-arg]
    return tuple(Word(t[0], t[1], x.strip()) for t, x in chunks)


# --- the finish path ------------------------------------------------------------------------------


def test_finish_times_each_flicker_word_on_the_transcript_not_spread_over_the_line() -> (
    None
):
    """``time_lines(per_word=True)`` (finish, English flicker) feeds ``build_line_cues`` the heard times."""

    lines = [
        CaptionLine("l2", "Please— open the door!", performed="Please— open the door!")
    ]
    words = load_words_from(SCP_CHUNKS[7:11])
    timing = time_lines(
        lines, duration=15.104, words=words, spans=_speech, per_word=True
    )
    cues = [c for g in build_line_cues([lines[0].text], list(timing.anchors), holds=timing.holds,
                                       word_cues=timing.word_cues) for c in g]  # fmt: skip
    assert _starts(cues, "open") == [pytest.approx(8.83, abs=0.02)]
    assert _starts(cues, "door") == [pytest.approx(9.15)]
    # Spread over the 7.83-10.38 s span by length, "door!" would start near 9.6 s.
    spread = [c for g in build_line_cues([lines[0].text], list(timing.anchors), holds=timing.holds)
              for c in g]  # fmt: skip
    assert _starts(spread, "door")[0] > 9.4


def test_load_words_reads_the_desk_transcript(tmp_path: Path) -> None:
    assert load_words(_words_json(tmp_path))[1].text == "-9341."
